"""
Camera configuration helpers: vendor presets, URL building, credential
redaction, the network allowlist, and the admin connection test.

The connection test makes outbound requests on behalf of an admin, so every
target is checked against GATE_CAMERA_ALLOWED_CIDRS on its resolved address,
and the request is made to that resolved address to rule out DNS rebinding.
"""

import ipaddress
import logging
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote

import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

logger = logging.getLogger(__name__)

VENDOR_PRESETS = {
    'hikvision': {
        'main_stream_path': '/Streaming/Channels/101',
        'sub_stream_path': '/Streaming/Channels/102',
        'snapshot_path': '/ISAPI/Streaming/channels/101/picture',
        # Sub-stream picture: much cheaper for the camera to encode
        'live_snapshot_path': '/ISAPI/Streaming/channels/102/picture',
    },
    'dahua': {
        'main_stream_path': '/cam/realmonitor?channel=1&subtype=0',
        'sub_stream_path': '/cam/realmonitor?channel=1&subtype=1',
        'snapshot_path': '/cgi-bin/snapshot.cgi',
        'live_snapshot_path': '/cgi-bin/snapshot.cgi?channel=1&subtype=1',
    },
    'generic': {
        'main_stream_path': '',
        'sub_stream_path': '',
        'snapshot_path': '',
        'live_snapshot_path': '',
    },
}

TEST_TIMEOUT_SECONDS = 5
SNAPSHOT_MAX_BYTES = 5 * 1024 * 1024

_HOSTNAME_LABEL = re.compile(r'^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$')
_URL_CREDENTIALS = re.compile(r'(://[^:/@\s]*):[^@\s]*@')


class CameraHostError(ValueError):
    """The camera host is malformed or outside the allowed networks."""


def apply_preset(camera):
    """Fill empty stream/snapshot paths from the camera's vendor preset."""
    for name, value in VENDOR_PRESETS.get(camera.vendor, {}).items():
        if not getattr(camera, name):
            setattr(camera, name, value)


def validate_host_syntax(host):
    host = (host or '').strip()
    if not host:
        raise CameraHostError('Host is required.')
    if any(c in host for c in ':/@? \\'):
        raise CameraHostError('Enter only an IP address or hostname, without scheme, port, path or credentials.')
    try:
        ipaddress.IPv4Address(host)
        return host
    except ValueError:
        pass
    if host.replace('.', '').isdigit():
        raise CameraHostError(f'"{host}" is not a valid IPv4 address.')
    if len(host) > 253 or not all(_HOSTNAME_LABEL.match(label) for label in host.split('.')):
        raise CameraHostError(f'"{host}" is not a valid IPv4 address or hostname.')
    return host


def allowed_networks():
    return [ipaddress.ip_network(c, strict=False) for c in settings.GATE_CAMERA_ALLOWED_CIDRS]


def resolve_allowed_host(host):
    """
    Validate and resolve a camera host. Returns the IPv4 address to connect to.
    Raises CameraHostError if malformed, unresolvable, or any resolved address
    is outside GATE_CAMERA_ALLOWED_CIDRS.
    """
    host = validate_host_syntax(host)
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        raise CameraHostError(f'Cannot resolve host "{host}".')
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise CameraHostError(f'Cannot resolve host "{host}".')
    networks = allowed_networks()
    for addr in addresses:
        ip = ipaddress.IPv4Address(addr)
        if not any(ip in net for net in networks):
            raise CameraHostError(
                f'{addr} is outside the allowed camera networks '
                f'({", ".join(settings.GATE_CAMERA_ALLOWED_CIDRS)}). '
                f'Add its range to GATE_CAMERA_ALLOWED_CIDRS if it is an internal address.'
            )
    return addresses[0]


def _userinfo(username, password):
    if not username:
        return ''
    info = quote(username, safe='')
    if password:
        info += ':' + quote(password, safe='')
    return info + '@'


def rtsp_url(camera, password, stream='main', host=None):
    path = camera.main_stream_path if stream == 'main' else camera.sub_stream_path
    return (
        f'rtsp://{_userinfo(camera.username, password)}{host or camera.host}:'
        f'{camera.rtsp_port}{_leading_slash(path)}'
    )


def snapshot_url(camera, host=None):
    return f'http://{host or camera.host}:{camera.http_port}{_leading_slash(camera.snapshot_path)}'


def redact_url(url):
    """Replace the password in a URL with ***."""
    return _URL_CREDENTIALS.sub(r'\1:***@', url or '')


def _leading_slash(path):
    if not path:
        return ''
    return path if path.startswith('/') else '/' + path


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------

@dataclass
class TestStep:
    name: str
    ok: Optional[bool]
    message: str

    def as_dict(self):
        return {'name': self.name, 'ok': self.ok, 'message': self.message}


@dataclass
class ConnectionTestResult:
    steps: List[TestStep] = field(default_factory=list)
    image: Optional[bytes] = None

    @property
    def ok(self):
        return all(s.ok is not False for s in self.steps) and any(s.ok for s in self.steps)

    @property
    def error(self):
        failed = [s.message for s in self.steps if s.ok is False]
        return '; '.join(failed)


def test_connection(camera, password):
    """
    Check that a camera is reachable and authenticates. `camera` may be an
    unsaved Camera instance. Returns a ConnectionTestResult; never raises for
    network failures.
    """
    result = ConnectionTestResult()

    try:
        ip = resolve_allowed_host(camera.host)
    except CameraHostError as exc:
        result.steps.append(TestStep('host', False, str(exc)))
        return result
    result.steps.append(TestStep('host', True, f'{camera.host} resolves to {ip}'))

    result.steps.append(_check_tcp(ip, camera.rtsp_port))

    if not camera.snapshot_path:
        result.steps.append(TestStep('snapshot', None, 'No snapshot path configured; skipped'))
        return result

    step, image = _fetch_snapshot(camera, ip, password)
    result.steps.append(step)
    result.image = image
    return result


# ---------------------------------------------------------------------------
# Live view
#
# The connection test opens a fresh connection per call, which is right for a
# one-off check. The live view runs continuously, so it keeps one session per
# camera with the authentication already negotiated: a fresh Digest handshake
# would cost a second request for every frame. Viewers share one fetcher, so
# the camera sees the same load whether one guard or five are watching.
# ---------------------------------------------------------------------------

HOST_RESOLVE_TTL_SECONDS = 60

_fetchers = {}
_fetchers_lock = threading.Lock()


def live_snapshot_url(camera, host=None, fallback=False):
    """The live view prefers the camera's low-resolution snapshot path."""
    path = camera.snapshot_path if fallback else (camera.live_snapshot_path or camera.snapshot_path)
    return f'http://{host or camera.host}:{camera.http_port}{_leading_slash(path)}'


def _live_signature(camera):
    """Everything that invalidates a kept-open session."""
    return (
        camera.host, camera.http_port, camera.username,
        camera.live_snapshot_path or camera.snapshot_path, camera.config_version,
    )


class _LiveFetcher:
    """One kept-open session per camera, shared by every viewer."""

    def __init__(self, camera):
        self.signature = _live_signature(camera)
        self.session = requests.Session()
        self.auth = None
        self.lock = threading.Lock()
        self.ip = None
        self.ip_at = 0.0
        self.frame = None
        self.error = ''
        self.fetched_at = 0.0
        # Vendor presets guess the sub-stream path; not every camera has one.
        self.fallback = False

    def get(self, camera, min_interval):
        with self.lock:
            # Another viewer may have just fetched while this thread waited.
            if self.fetched_at and time.monotonic() - self.fetched_at < min_interval:
                return self.frame, self.error
            self._fetch(camera)
            return self.frame, self.error

    def _resolve(self, camera):
        if self.ip and time.monotonic() - self.ip_at < HOST_RESOLVE_TTL_SECONDS:
            return self.ip
        self.ip = resolve_allowed_host(camera.host)
        self.ip_at = time.monotonic()
        return self.ip

    def _fetch(self, camera):
        self.fetched_at = time.monotonic()
        try:
            ip = self._resolve(camera)
        except CameraHostError as exc:
            self.frame, self.error = None, str(exc)
            return
        try:
            password = camera.get_password()
        except Exception as exc:
            self.frame, self.error = None, f'Stored password cannot be read: {exc}'
            return

        url = live_snapshot_url(camera, host=ip, fallback=self.fallback)
        headers = {'Host': f'{camera.host}:{camera.http_port}'}
        deadline = time.monotonic() + TEST_TIMEOUT_SECONDS
        try:
            response = self.session.get(
                url, headers=headers, auth=self.auth, timeout=TEST_TIMEOUT_SECONDS,
                allow_redirects=False, stream=True,
            )
            if response.status_code == 401 and camera.username:
                # Negotiate once; the scheme is then reused for every later frame.
                challenge = response.headers.get('WWW-Authenticate', '').lower()
                response.close()
                if 'digest' in challenge:
                    self.auth = HTTPDigestAuth(camera.username, password or '')
                else:
                    self.auth = HTTPBasicAuth(camera.username, password or '')
                response = self.session.get(
                    url, headers=headers, auth=self.auth, timeout=TEST_TIMEOUT_SECONDS,
                    allow_redirects=False, stream=True,
                )
            status = response.status_code
            step, image = _read_snapshot(response, deadline)
        except requests.Timeout:
            self.frame, self.error = None, 'Snapshot request timed out'
            return
        except requests.RequestException as exc:
            self.frame, self.error = None, f'Snapshot request failed: {exc.__class__.__name__}'
            return

        if status == 401:
            # Credentials changed on the camera: negotiate again next time.
            self.auth = None
        if status == 404 and not self.fallback and camera.live_snapshot_path                 and camera.snapshot_path and camera.live_snapshot_path != camera.snapshot_path:
            logger.warning(
                'Camera %s has no live snapshot path %s; falling back to %s',
                camera.pk, camera.live_snapshot_path, camera.snapshot_path,
            )
            self.fallback = True
        if image is None:
            self.frame, self.error = None, step.message
        else:
            self.frame, self.error = image, ''


def _fetcher_for(camera):
    signature = _live_signature(camera)
    with _fetchers_lock:
        fetcher = _fetchers.get(camera.pk)
        if fetcher is None or fetcher.signature != signature:
            fetcher = _LiveFetcher(camera)
            _fetchers[camera.pk] = fetcher
        return fetcher


def forget_live_session(camera_pk):
    """Drop a camera's kept-open session and stream, e.g. after it is deleted."""
    with _fetchers_lock:
        fetcher = _fetchers.pop(camera_pk, None)
        reader = _rtsp_readers.pop(camera_pk, None)
    if fetcher is not None:
        fetcher.session.close()
    if reader is not None:
        reader.stop()


# ---------------------------------------------------------------------------
# Live view over RTSP
#
# A snapshot is a separate HTTP request the camera has to encode and answer,
# and a camera already serving the gate agent may start refusing them. A
# browser cannot play RTSP, so with GATE_LIVE_VIEW_SOURCE=rtsp the server holds
# one stream per camera open (the sub-stream, cheap to decode) and hands out
# its newest frame as a JPEG. Every viewer shares that one stream, which is
# closed again once nobody has asked for a frame for GATE_LIVE_VIEW_IDLE_SECONDS.
#
# A request never waits for the stream: until its first frame arrives, or
# while it cannot be opened, the view carries on with HTTP snapshots.
# ---------------------------------------------------------------------------

RTSP_RETRY_SECONDS = 15
RTSP_JPEG_QUALITY = 80

_rtsp_readers = {}


def live_stream_url(camera, password, host=None):
    """The stream the live view reads: the sub-stream, or the main one if there is none."""
    stream = 'sub' if camera.sub_stream_path else 'main'
    return rtsp_url(camera, password, stream=stream, host=host)


def _rtsp_signature(camera):
    return (
        camera.host, camera.rtsp_port, camera.username,
        camera.sub_stream_path or camera.main_stream_path, camera.config_version,
    )


class _RtspLiveReader:
    """One open RTSP stream per camera, read in the background, shared by every viewer."""

    def __init__(self, camera):
        self.camera_pk = camera.pk
        self.signature = _rtsp_signature(camera)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.frame = None
        self.seq = 0
        self.jpeg = None
        self.jpeg_seq = 0
        self.error = ''
        self.failed_at = 0.0
        self.last_request = 0.0

    def get(self, camera):
        """The newest frame as JPEG, or (None, why not). Never blocks on the camera."""
        with self.lock:
            self.last_request = time.monotonic()
            running = self.thread is not None and self.thread.is_alive()
            if not running:
                if self.failed_at and time.monotonic() - self.failed_at < RTSP_RETRY_SECONDS:
                    return None, self.error
                error = self._start(camera)
                if error:
                    return None, error
                return None, 'Opening the camera stream'
            if self.frame is None:
                return None, 'Opening the camera stream'
            if self.jpeg_seq != self.seq:
                self.jpeg = _encode_jpeg(self.frame)
                self.jpeg_seq = self.seq
            return self.jpeg, ''

    def _start(self, camera):
        # Resolved and decrypted here: the reader thread never touches the database
        try:
            ip = resolve_allowed_host(camera.host)
            password = camera.get_password()
        except CameraHostError as exc:
            return self._fail(str(exc))
        except Exception as exc:
            return self._fail(f'Stored password cannot be read: {exc}')
        self.frame, self.error = None, ''
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run, args=(live_stream_url(camera, password, host=ip),),
            name=f'live-rtsp-{camera.pk}', daemon=True,
        )
        self.thread.start()
        return ''

    def _fail(self, error):
        self.error = error
        self.failed_at = time.monotonic()
        return error

    def _run(self, url):
        capture = None
        try:
            import cv2
            # Over UDP a lost packet stalls the stream; FFmpeg reads these at open time only
            micros = TEST_TIMEOUT_SECONDS * 1_000_000
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
                f'rtsp_transport;tcp|stimeout;{micros}|timeout;{micros}|max_delay;500000'
            )
            capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not capture.isOpened():
                raise RuntimeError('Cannot open the RTSP stream')
            logger.info('Live view of camera %s reading %s', self.camera_pk, redact_url(url))
            idle = settings.GATE_LIVE_VIEW_IDLE_SECONDS
            while not self.stop_event.is_set():
                if time.monotonic() - self.last_request > idle:
                    logger.info('Live view of camera %s idle; closing its stream', self.camera_pk)
                    break
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError('The RTSP stream stopped sending frames')
                with self.lock:
                    self.frame = frame
                    self.seq += 1
        except Exception as exc:
            logger.warning('Live view of camera %s: %s', self.camera_pk, exc)
            with self.lock:
                self._fail(f'RTSP: {exc}')
                self.frame = None
        finally:
            if capture is not None:
                capture.release()

    def stop(self):
        self.stop_event.set()


def _encode_jpeg(frame):
    import cv2
    ok, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, RTSP_JPEG_QUALITY])
    return buffer.tobytes() if ok else None


def _rtsp_reader_for(camera):
    signature = _rtsp_signature(camera)
    with _fetchers_lock:
        reader = _rtsp_readers.get(camera.pk)
        if reader is None or reader.signature != signature:
            if reader is not None:
                reader.stop()
            reader = _RtspLiveReader(camera)
            _rtsp_readers[camera.pk] = reader
        return reader


def live_snapshot(camera):
    """
    One frame from a saved camera for the live view. Returns (jpeg_bytes,
    error); never raises for network failures.

    With GATE_LIVE_VIEW_SOURCE=rtsp (the default) the frame comes from the
    camera's stream when one is configured and open. Otherwise, and meanwhile,
    it is a snapshot: the camera is asked at most once per
    GATE_SNAPSHOT_CACHE_SECONDS, however many viewers there are and however
    fast they refresh, since it also serves the gate agent and some cameras
    answer HTTP 500 when pushed harder. Failures are held for the same
    interval, so a struggling camera gets a break.
    """
    stream_error = ''
    if settings.GATE_LIVE_VIEW_SOURCE == 'rtsp' and (camera.sub_stream_path or camera.main_stream_path):
        image, stream_error = _rtsp_reader_for(camera).get(camera)
        if image is not None:
            return image, ''
    if not (camera.live_snapshot_path or camera.snapshot_path):
        return None, stream_error or 'No snapshot path configured for this camera.'
    min_interval = max(0.05, settings.GATE_SNAPSHOT_CACHE_SECONDS)
    image, error = _fetcher_for(camera).get(camera, min_interval)
    if image is None and stream_error:
        error = f'{stream_error}; snapshot: {error}'
    return image, error


def _check_tcp(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=TEST_TIMEOUT_SECONDS):
            return TestStep('rtsp_port', True, f'RTSP port {port} is reachable')
    except socket.timeout:
        return TestStep('rtsp_port', False, f'RTSP port {port}: connection timed out')
    except OSError as exc:
        return TestStep('rtsp_port', False, f'RTSP port {port}: {_os_error_text(exc)}')


def _os_error_text(exc):
    if isinstance(exc, ConnectionRefusedError):
        return 'connection refused'
    return exc.strerror or str(exc)


def _fetch_snapshot(camera, ip, password):
    """Fetch the snapshot, preferring Digest auth and falling back to Basic."""
    url = snapshot_url(camera, host=ip)
    headers = {'Host': f'{camera.host}:{camera.http_port}'}
    deadline = time.monotonic() + TEST_TIMEOUT_SECONDS

    try:
        response = _get(url, headers, auth=None, deadline=deadline)
        if response.status_code == 401 and camera.username:
            challenge = response.headers.get('WWW-Authenticate', '').lower()
            response.close()
            if 'digest' in challenge:
                auth = HTTPDigestAuth(camera.username, password or '')
            else:
                auth = HTTPBasicAuth(camera.username, password or '')
            response = _get(url, headers, auth=auth, deadline=deadline)
        return _read_snapshot(response, deadline)
    except requests.Timeout:
        return TestStep('snapshot', False, 'Snapshot request timed out'), None
    except requests.ConnectionError:
        return TestStep('snapshot', False, f'Snapshot: cannot connect to HTTP port {camera.http_port}'), None
    except requests.RequestException as exc:
        return TestStep('snapshot', False, f'Snapshot request failed: {exc.__class__.__name__}'), None


def _get(url, headers, auth, deadline):
    remaining = max(0.1, deadline - time.monotonic())
    return requests.get(
        url, headers=headers, auth=auth, timeout=remaining,
        allow_redirects=False, stream=True,
    )


def _read_snapshot(response, deadline):
    with response:
        status = response.status_code
        if status == 401:
            return TestStep('snapshot', False, 'Authentication failed: wrong username or password'), None
        if status == 404:
            return TestStep('snapshot', False, 'Snapshot path not found (404)'), None
        if 300 <= status < 400:
            return TestStep('snapshot', False, f'Camera redirected ({status}); redirects are not followed'), None
        if status != 200:
            return TestStep('snapshot', False, f'Camera returned HTTP {status}'), None

        content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
        if content_type not in ('image/jpeg', 'image/jpg'):
            return TestStep('snapshot', False, f'Expected a JPEG image, got "{content_type or "unknown"}"'), None

        data = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            data.extend(chunk)
            if len(data) > SNAPSHOT_MAX_BYTES:
                return TestStep('snapshot', False, 'Snapshot larger than 5 MB; aborted'), None
            if time.monotonic() > deadline:
                return TestStep('snapshot', False, 'Snapshot request timed out'), None

    return TestStep('snapshot', True, f'Received {len(data) // 1024} KB JPEG snapshot'), bytes(data)
