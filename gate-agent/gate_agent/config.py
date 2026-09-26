"""Agent settings from the environment. Camera and controller settings come from the LPR service."""

import os
from dataclasses import dataclass


def _float(name, default):
    return float(os.getenv(name, default))


def _int(name, default):
    return int(os.getenv(name, default))


def _bool(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == '':
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


@dataclass
class AgentSettings:
    api_url: str = 'http://lpr-app:8000'
    agent_token: str = ''
    metrics_port: int = 9101
    config_interval: float = 30.0
    command_poll_interval: float = 1.0
    status_interval: float = 15.0
    frame_interval: float = 0.2
    burst_interval: float = 0.4
    # Frames kept from before the trigger, so a burst can use the moment the
    # vehicle was in position instead of only what comes after.
    prebuffer_frames: int = 3
    # A vehicle that never stops is read anyway after this long in the zone
    # (0 disables, and only vehicles that come to a stop are read). Short
    # enough that a vehicle driving through is read while still in the zone.
    moving_read_seconds: float = 1.0
    # A shadow sweeping across the zone darkens it without changing its
    # texture; with the filter on it is not taken for a vehicle.
    shadow_filter: bool = True
    # Texture change (see trigger.texture_difference) at which a brightness
    # change counts as presence. Shadows measured 0.010-0.024, vehicles 0.028+.
    shadow_texture_threshold: float = 0.025
    # RTSP transport and socket timeout, read by RtspSource from the environment
    rtsp_transport: str = 'tcp'
    rtsp_timeout_seconds: float = 5.0
    presence_factor: float = 3.0
    max_attempts: int = 2
    max_occupied_seconds: float = 300.0
    request_timeout: float = 10.0
    jpeg_quality: int = 90

    @classmethod
    def from_env(cls):
        return cls(
            api_url=os.getenv('LPR_API_URL', cls.api_url).rstrip('/'),
            agent_token=os.getenv('GATE_AGENT_TOKEN', ''),
            metrics_port=_int('AGENT_METRICS_PORT', cls.metrics_port),
            config_interval=_float('AGENT_CONFIG_INTERVAL', cls.config_interval),
            command_poll_interval=_float('AGENT_COMMAND_POLL_INTERVAL', cls.command_poll_interval),
            status_interval=_float('AGENT_STATUS_INTERVAL', cls.status_interval),
            frame_interval=_float('AGENT_FRAME_INTERVAL', cls.frame_interval),
            burst_interval=_float('AGENT_BURST_INTERVAL', cls.burst_interval),
            prebuffer_frames=_int('AGENT_PREBUFFER_FRAMES', cls.prebuffer_frames),
            moving_read_seconds=_float('AGENT_MOVING_READ_SECONDS', cls.moving_read_seconds),
            shadow_filter=_bool('AGENT_SHADOW_FILTER', cls.shadow_filter),
            shadow_texture_threshold=_float('AGENT_SHADOW_TEXTURE_THRESHOLD', cls.shadow_texture_threshold),
            rtsp_transport=os.getenv('AGENT_RTSP_TRANSPORT', cls.rtsp_transport),
            rtsp_timeout_seconds=_float('AGENT_RTSP_TIMEOUT_SECONDS', cls.rtsp_timeout_seconds),
            presence_factor=_float('AGENT_PRESENCE_FACTOR', cls.presence_factor),
            max_attempts=_int('AGENT_MAX_ATTEMPTS', cls.max_attempts),
            max_occupied_seconds=_float('AGENT_MAX_OCCUPIED_SECONDS', cls.max_occupied_seconds),
            request_timeout=_float('AGENT_REQUEST_TIMEOUT', cls.request_timeout),
            jpeg_quality=_int('AGENT_JPEG_QUALITY', cls.jpeg_quality),
        )
