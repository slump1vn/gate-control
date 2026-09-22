"""Agent settings from the environment. Camera and controller settings come from the LPR service."""

import os
from dataclasses import dataclass


def _float(name, default):
    return float(os.getenv(name, default))


def _int(name, default):
    return int(os.getenv(name, default))


@dataclass
class AgentSettings:
    api_url: str = 'http://lpr-app:8000'
    agent_token: str = ''
    metrics_port: int = 9101
    config_interval: float = 30.0
    command_poll_interval: float = 1.0
    status_interval: float = 15.0
    frame_interval: float = 0.4
    burst_interval: float = 0.4
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
            presence_factor=_float('AGENT_PRESENCE_FACTOR', cls.presence_factor),
            max_attempts=_int('AGENT_MAX_ATTEMPTS', cls.max_attempts),
            max_occupied_seconds=_float('AGENT_MAX_OCCUPIED_SECONDS', cls.max_occupied_seconds),
            request_timeout=_float('AGENT_REQUEST_TIMEOUT', cls.request_timeout),
            jpeg_quality=_int('AGENT_JPEG_QUALITY', cls.jpeg_quality),
        )
