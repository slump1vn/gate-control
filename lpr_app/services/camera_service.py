"""
Camera configuration helpers: vendor presets, URL building, credential
redaction, the network allowlist, and the admin connection test.

The connection test makes outbound requests on behalf of an admin, so every
target is checked against GATE_CAMERA_ALLOWED_CIDRS on its resolved address,
and the request is made to that resolved address to rule out DNS rebinding.
"""

import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote

import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

VENDOR_PRESETS = {
    'hikvision': {
        'main_stream_path': '/Streaming/Channels/101',
        'sub_stream_path': '/Streaming/Channels/102',
        'snapshot_path': '/ISAPI/Streaming/channels/101/picture',
    },
    'dahua': {
        'main_stream_path': '/cam/realmonitor?channel=1&subtype=0',
        'sub_stream_path': '/cam/realmonitor?channel=1&subtype=1',
        'snapshot_path': '/cgi-bin/snapshot.cgi',
    },
    'generic': {
        'main_stream_path': '',
        'sub_stream_path': '',
        'snapshot_path': '',
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


def live_snapshot(camera):
    """
    Fetch one frame from a saved camera for the live view, using its stored
    password. Returns (jpeg_bytes, error); never raises for network failures.

    Frames are cached briefly so several viewers (or a fast refresh interval)
    cannot hammer the camera.
    """
    from django.core.cache import cache

    if not camera.snapshot_path:
        return None, 'No snapshot path configured for this camera.'

    key = f'camera-snapshot:{camera.pk}'
    cached = cache.get(key)
    if cached is not None:
        # A cached failure is held for the same interval, so a camera that is
        # struggling is not asked again on every refresh.
        return (None, cached['error']) if cached.get('error') else (cached['image'], '')

    try:
        ip = resolve_allowed_host(camera.host)
    except CameraHostError as exc:
        return None, str(exc)

    try:
        password = camera.get_password()
    except Exception as exc:
        return None, f'Stored password cannot be read: {exc}'

    ttl = max(0.05, settings.GATE_SNAPSHOT_CACHE_SECONDS)
    step, image = _fetch_snapshot(camera, ip, password)
    if image is None:
        cache.set(key, {'error': step.message}, ttl)
        return None, step.message

    cache.set(key, {'image': image}, ttl)
    return image, ''


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
