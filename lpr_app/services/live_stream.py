"""
Live video through go2rtc.

The JPEG live view (camera_service.live_snapshot) decodes the stream on the
server and re-encodes one frame per request, which keeps it to a few frames a
second. With GATE_LIVE_STREAM_API set, a go2rtc container relays the camera's
H.264 sub-stream to the browser as it is (MSE over a WebSocket), at the
camera's own frame rate.

go2rtc has no login of its own, and its API lists every stream with the
camera's credentials in it, so it is never exposed. The live-gateway (nginx)
passes only the player's WebSocket through, after asking authorize_request
whether the viewer may watch that camera; this is also where the stream is
registered with go2rtc, so camera addresses and passwords stay in the
database and go2rtc only ever holds them in memory.
"""

import logging
import re
import threading
from urllib.parse import parse_qs, urlsplit

import requests
from django.conf import settings

from .camera_service import CameraHostError, _rtsp_signature, live_stream_url, resolve_allowed_host

logger = logging.getLogger(__name__)

API_TIMEOUT_SECONDS = 5
STREAM_NAME = re.compile(r'^camera-(\d+)$')

# camera pk -> signature of the stream registered with go2rtc by this process.
# A changed or deleted camera changes or loses its signature, so nothing needs
# forgetting: a deleted camera is never authorized again.
_registered = {}
_lock = threading.Lock()


class LiveStreamError(Exception):
    pass


def enabled():
    return bool(settings.GATE_LIVE_STREAM_API)


def stream_name(camera):
    return f'camera-{camera.pk}'


def player_url(camera):
    """Where the browser opens the camera's video WebSocket, or None when live video is off."""
    if not enabled() or not (camera.sub_stream_path or camera.main_stream_path):
        return None
    base = settings.GATE_LIVE_STREAM_PATH.rstrip('/')
    return f'{base}/api/ws?src={stream_name(camera)}'


def camera_id_from_uri(uri):
    """The camera a player URI (/live/api/ws?src=camera-3) asks for, or None."""
    values = parse_qs(urlsplit(uri or '').query).get('src') or []
    if len(values) != 1:
        return None
    match = STREAM_NAME.match(values[0])
    return int(match.group(1)) if match else None


def _api(method, params):
    url = settings.GATE_LIVE_STREAM_API.rstrip('/') + '/api/streams'
    try:
        return requests.request(method, url, params=params, timeout=API_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise LiveStreamError(f'go2rtc unreachable: {type(exc).__name__}') from exc


def ensure_stream(camera):
    """
    Make sure go2rtc knows the camera's current stream. go2rtc opens the
    camera only while someone watches, so registering costs nothing.
    """
    name = stream_name(camera)
    signature = _rtsp_signature(camera)
    with _lock:
        if _registered.get(camera.pk) == signature and _api('GET', {'src': name}).status_code == 200:
            return
        try:
            ip = resolve_allowed_host(camera.host)
        except CameraHostError as exc:
            raise LiveStreamError(str(exc)) from exc
        try:
            password = camera.get_password()
        except Exception as exc:
            raise LiveStreamError(f'Stored password cannot be read: {exc}') from exc
        # Replace whatever go2rtc holds under this name: the camera may have changed
        _api('DELETE', {'src': name})
        # go2rtc runs without a config file, so it answers "config file disabled"
        # (400) after creating the stream in memory; whether it exists is what counts
        _api('PUT', {'name': name, 'src': live_stream_url(camera, password, host=ip)})
        if _api('GET', {'src': name}).status_code != 200:
            raise LiveStreamError('go2rtc did not accept the stream')
        _registered[camera.pk] = signature
        logger.info('Live video of camera %s registered with go2rtc', camera.pk)

