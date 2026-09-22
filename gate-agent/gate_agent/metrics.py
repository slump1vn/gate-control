"""Prometheus metrics exported by the agent on AGENT_METRICS_PORT."""

from prometheus_client import Counter, Histogram, start_http_server

FRAMES = Counter('lpr_gate_agent_frames_total', 'Camera frames grabbed', ['gate', 'result'])
TRIGGERS = Counter('lpr_gate_agent_triggers_total', 'Recognition bursts triggered', ['gate'])
DECISIONS = Counter('lpr_gate_agent_decisions_total', 'Decisions received', ['gate', 'decision', 'reason'])
DECISION_ROUNDTRIP = Histogram(
    'lpr_gate_agent_decision_roundtrip_seconds', 'Burst capture plus decision request',
    ['gate'], buckets=[0.5, 1, 2, 3, 5, 8, 13, 21, float('inf')],
)
COMMANDS = Counter('lpr_gate_agent_commands_total', 'Controller commands sent', ['gate', 'command', 'result'])


def serve(port):
    start_http_server(port)
