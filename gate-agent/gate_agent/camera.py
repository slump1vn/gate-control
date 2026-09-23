"""
Frame sources and frame preparation.

Two ways to read a camera. Snapshot over HTTP needs no video decoding and
gives full resolution, but costs the camera a request per frame, and some
refuse when asked more than once or twice a second. RTSP costs one connection
whatever the frame rate, so it is the way to watch a lane quickly; it needs
OpenCV, imported only when used. Hikvision/Dahua need Digest auth, handled
here. A directory source replays recorded frames for testing without a camera.
"""

import io
import os
import re
import threading
import time
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
    """
    A live RTSP stream, read continuously in a background thread.

    The camera serves one connection instead of answering a snapshot request
    per frame, which is far cheaper for it and allows a much higher frame
    rate. The thread matters: OpenCV buffers decoded frames, so a caller that
    reads slower than the stream would get progressively older frames — the
    plate of a vehicle that has already gone. The reader keeps only the latest
    frame and drops the rest.
    """

    START_TIMEOUT = 10.0

    def __init__(self, url, frame_timeout=5.0, transport=None, socket_timeout=None):
        self.url = url
        self.frame_timeout = frame_timeout
        # Default to TCP: over UDP a lost packet stalls the stream, and FFmpeg
        # then sits on its own 30s timeout before anyone notices.
        self.transport = transport or os.getenv('AGENT_RTSP_TRANSPORT', 'tcp')
        self.socket_timeout = float(socket_timeout if socket_timeout is not None
                                    else os.getenv('AGENT_RTSP_TIMEOUT_SECONDS', 5.0))
        self.capture = None
        self.cv2 = None
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.seq = 0
        self.taken = 0
        self.error = None
        self.new_frame = threading.Condition(self.lock)

    def _open(self):
        try:
            import cv2
        except ImportError:
            raise CameraError('RTSP needs opencv-python-headless, which is not installed')
        self.cv2 = cv2
        # FFmpeg reads these when the capture is opened, not before or after.
        micros = int(max(1.0, self.socket_timeout) * 1_000_000)
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
            f'rtsp_transport;{self.transport}|stimeout;{micros}|timeout;{micros}|max_delay;500000'
        )
        capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not capture.isOpened():
            raise CameraError('Cannot open RTSP stream')
        try:
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:  # pragma: no cover - not every backend supports it
            pass
        self.capture = capture
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._read_forever, name='rtsp-reader', daemon=True)
        self.thread.start()

    def _read_forever(self):
        while not self.stop_event.is_set():
            ok, frame = self.capture.read()
            with self.new_frame:
                if not ok:
                    self.error = 'RTSP stream returned no frame'
                    self.new_frame.notify_all()
                    return
                self.latest = frame
                self.seq += 1
                self.new_frame.notify_all()

    def grab(self):
        if self.capture is None:
            self._open()
        deadline = time.monotonic() + self.frame_timeout
        with self.new_frame:
            while self.seq == self.taken and self.error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.new_frame.wait(remaining)
            error, frame, seq = self.error, self.latest, self.seq
            self.taken = seq
        if error:
            self.close()
            raise CameraError(error)
        if frame is None:
            self.close()
            raise CameraError('No frame from the RTSP stream')
        return Image.fromarray(self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB))

    def close(self):
        self.stop_event.set()
        thread, self.thread = self.thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        with self.new_frame:
            self.latest = None
            self.seq = self.taken = 0
            self.error = None

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
