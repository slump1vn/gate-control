"""Prometheus metrics exported by the agent on AGENT_METRICS_PORT."""

from prometheus_client import Counter, Gauge, Histogram, start_http_server

FRAMES = Counter('lpr_gate_agent_frames_total', 'Camera frames grabbed', ['gate', 'result'])
TRIGGERS = Counter('lpr_gate_agent_triggers_total', 'Recognition bursts triggered', ['gate'])
# Measured, not configured: a slow camera or a slow network lowers it
FPS = Gauge('lpr_gate_agent_fps', 'Frames per second actually grabbed', ['gate'])
# What the trigger is seeing right now, against its thresholds. When a vehicle
# arrives and nothing happens, these say whether it was even noticed.
MOTION = Gauge('lpr_gate_agent_motion', 'Last motion score (0-1)', ['gate'])
PRESENCE = Gauge('lpr_gate_agent_presence', 'Last presence score (0-1)', ['gate'])
PRESENCE_THRESHOLD = Gauge('lpr_gate_agent_presence_threshold', 'Presence score needed', ['gate'])
GRAB_SECONDS = Histogram(
    'lpr_gate_agent_grab_seconds', 'Time to grab one frame from the camera',
    ['gate'], buckets=[0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, float('inf')],
)
DECISIONS = Counter('lpr_gate_agent_decisions_total', 'Decisions received', ['gate', 'decision', 'reason'])
DECISION_ROUNDTRIP = Histogram(
    'lpr_gate_agent_decision_roundtrip_seconds', 'Burst capture plus decision request',
    ['gate'], buckets=[0.5, 1, 2, 3, 5, 8, 13, 21, float('inf')],
)
COMMANDS = Counter('lpr_gate_agent_commands_total', 'Controller commands sent', ['gate', 'command', 'result'])


def serve(port):
    start_http_server(port)
