"""
Frame sources and frame preparation.

Snapshot over HTTP is preferred: no video decoding, full resolution, and
Hikvision/Dahua cameras need Digest auth, which is handled here. RTSP needs
OpenCV and is only imported when a camera has no snapshot endpoint. A
directory source replays recorded frames for testing without a camera.
"""

import io
import os
import re
from urllib.parse import quote

import requests
from PIL import Image
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp')
_URL_CREDENTIALS = re.compile(r'(://[^:/@\s]*):[^@\s]*@')


class CameraError(Exception):
    status = 'unreachable'


class CameraAuthError(CameraError):
    status = 'auth_failed'


class EndOfSource(Exception):
    """A replay source has no more frames."""


def redact(url):
    return _URL_CREDENTIALS.sub(r'\1:***@', url or '')


def _path(path):
    if not path:
        return ''
    return path if path.startswith('/') else '/' + path


class SnapshotSource:
    def __init__(self, host, port, path, username='', password='', timeout=5.0, session=None):
        self.url = f'http://{host}:{port}{_path(path)}'
        self.username = username
        self.password = password or ''
        self.timeout = timeout
        self.session = session or requests.Session()
        self.auth = None

    def _choose_auth(self, response):
        challenge = response.headers.get('WWW-Authenticate', '').lower()
        if 'digest' in challenge:
            return HTTPDigestAuth(self.username, self.password)
        return HTTPBasicAuth(self.username, self.password)

    def grab(self):
        try:
            response = self.session.get(self.url, auth=self.auth, timeout=self.timeout, allow_redirects=False)
            if response.status_code == 401 and self.username and self.auth is None:
                self.auth = self._choose_auth(response)
                response = self.session.get(self.url, auth=self.auth, timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as exc:
            raise CameraError(f'Snapshot request failed: {exc.__class__.__name__}')
        if response.status_code == 401:
            self.auth = None
            raise CameraAuthError('Camera rejected the username or password')
        if response.status_code != 200:
            raise CameraError(f'Camera returned HTTP {response.status_code}')
        try:
            image = Image.open(io.BytesIO(response.content))
            image.load()
        except Exception:
            raise CameraError('Camera returned something that is not an image')
        return image.convert('RGB')

    def close(self):
        self.session.close()

    def describe(self):
        return self.url


class RtspSource:
    def __init__(self, url):
        self.url = url
        self.capture = None

    def _open(self):
        try:
            import cv2
        except ImportError:
            raise CameraError('RTSP needs opencv-python-headless, which is not installed')
        self.cv2 = cv2
        self.capture = cv2.VideoCapture(self.url)
        if not self.capture.isOpened():
            self.capture = None
            raise CameraError('Cannot open RTSP stream')

    def grab(self):
        if self.capture is None:
            self._open()
        ok, frame = self.capture.read()
        if not ok:
            self.close()
            raise CameraError('RTSP stream returned no frame')
        return Image.fromarray(self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB))

    def close(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def describe(self):
        return redact(self.url)


class DirectorySource:
    """Replays image files in name order, for testing without a camera."""

    def __init__(self, directory):
        self.files = sorted(
            os.path.join(directory, name) for name in os.listdir(directory)
            if name.lower().endswith(IMAGE_EXTENSIONS)
        )
        self.index = 0

    def grab(self):
        if self.index >= len(self.files):
            raise EndOfSource()
        path = self.files[self.index]
        self.index += 1
        with Image.open(path) as image:
            return image.convert('RGB')

    def close(self):
        pass

    def describe(self):
        return f'{len(self.files)} files'


def rtsp_url(camera, stream='main'):
    path = camera.get('main_stream_path') if stream == 'main' else camera.get('sub_stream_path')
    userinfo = ''
    if camera.get('username'):
        userinfo = quote(camera['username'], safe='')
        if camera.get('password'):
            userinfo += ':' + quote(camera['password'], safe='')
        userinfo += '@'
    return f"rtsp://{userinfo}{camera['host']}:{camera.get('rtsp_port', 554)}{_path(path)}"


def build_source(camera, session=None):
    """Pick the frame source for a camera config from the LPR service."""
    if camera.get('credential_error'):
        raise CameraAuthError('Camera password cannot be decrypted by the LPR service; re-enter it')
    if camera.get('prefer_snapshot', True) and camera.get('snapshot_path'):
        return SnapshotSource(
            camera['host'], camera.get('http_port', 80), camera['snapshot_path'],
            camera.get('username', ''), camera.get('password') or '', session=session,
        )
    if camera.get('main_stream_path'):
        return RtspSource(rtsp_url(camera))
    raise CameraError('Camera has neither a snapshot path nor a stream path')


def crop_roi(image, roi):
    """Crop to a normalised {x, y, w, h} region; the full frame when roi is None."""
    if not roi:
        return image
    width, height = image.size
    left = max(0, int(roi['x'] * width))
    top = max(0, int(roi['y'] * height))
    right = min(width, int((roi['x'] + roi['w']) * width))
    bottom = min(height, int((roi['y'] + roi['h']) * height))
    if right <= left or bottom <= top:
        return image
    return image.crop((left, top, right, bottom))


def encode_jpeg(image, max_bytes, quality=90, min_quality=40):
    """
    Encode at `quality`, stepping down only when the frame exceeds max_bytes.
    Returns (bytes, quality_used). Raises ValueError if it cannot fit.
    """
    q = quality
    while True:
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=q)
        data = buffer.getvalue()
        if len(data) <= max_bytes:
            return data, q
        if q <= min_quality:
            raise ValueError(f'Frame is {len(data)} bytes at quality {q}, above the {max_bytes} byte limit')
        q = max(min_quality, q - 10)
