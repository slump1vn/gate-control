"""
Command line:

  python -m gate_agent run
      Watch every enabled gate's camera and act on decisions (the service).

  python -m gate_agent decide --gate ID photo1.jpg [photo2.jpg ...]
      Send photos as one burst and print the decision. Test recognition
      without a camera.

  python -m gate_agent replay --gate ID DIRECTORY [--fps 2.5]
      Feed recorded frames through the presence trigger and decisions as if
      they came from the camera.

decide and replay only open a *simulated* barrier unless
--allow-real-controller is given.
"""

import argparse
import json
import logging
import signal
import sys

from PIL import Image

from . import __version__, metrics
from .agent import Agent, GateWorker, make_controller, send_and_report
from .api import ApiError, LprApi
from .camera import DirectorySource, crop_roi, encode_jpeg
from .config import AgentSettings

logger = logging.getLogger('gate_agent')


def _api(settings):
    if not settings.agent_token:
        sys.exit('GATE_AGENT_TOKEN is not set')
    return LprApi(settings.api_url, settings.agent_token, timeout=settings.request_timeout)


def _load_config(api):
    try:
        return api.agent_config()
    except ApiError as exc:
        sys.exit(f'Cannot load the gate config from the LPR service: {exc}')
    except Exception as exc:
        sys.exit(f'Cannot reach the LPR service: {exc.__class__.__name__}: {exc}')


def _gate(config, gate_id):
    for gate in config.get('gates', []):
        if gate['id'] == gate_id:
            return gate
    sys.exit(f'Gate {gate_id} is not enabled or does not exist '
             f'(enabled: {[g["id"] for g in config.get("gates", [])]})')


def _may_actuate(gate, allow_real):
    return gate.get('controller_type') == 'simulator' or allow_real


def cmd_run(settings, args):
    api = _api(settings)
    metrics.serve(settings.metrics_port)
    agent = Agent(settings, api)

    def stop(*_):
        logger.info('Stopping')
        agent.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info('gate-agent %s starting; LPR service %s', __version__, settings.api_url)
    agent.run_forever()
    return 0


def cmd_decide(settings, args):
    api = _api(settings)
    config = _load_config(api)
    gate = _gate(config, args.gate)
    roi = (gate.get('camera') or {}).get('roi') if args.roi else None
    max_bytes = config.get('max_upload_bytes', 2 * 1024 * 1024)

    frames = []
    for path in args.images:
        with Image.open(path) as image:
            data, _ = encode_jpeg(crop_roi(image.convert('RGB'), roi), max_bytes, quality=settings.jpeg_quality)
        frames.append(data)

    try:
        result = api.decide(gate['id'], frames, timeout=config.get('decide_timeout_seconds', 8) + 5)
    except ApiError as exc:
        print(f'Decision request failed: {exc}', file=sys.stderr)
        return 2

    if result.get('actuate'):
        if _may_actuate(gate, args.allow_real_controller):
            result['command_sent'] = send_and_report(api, make_controller(gate), gate['name'], 'open', result['event_id'])
        else:
            api.command_result(result['event_id'], False, 'not_sent_cli_real_controller')
            result['command_sent'] = False
            result['note'] = 'Real controller not actuated; pass --allow-real-controller to open it.'
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get('decision') == 'granted' else 1


class VirtualClock:
    """Replay time: each frame advances the clock by 1/fps, however fast frames are processed."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def cmd_replay(settings, args):
    api = _api(settings)
    config = _load_config(api)
    gate = _gate(config, args.gate)
    if not gate.get('camera'):
        sys.exit('The gate has no enabled camera; the replay uses its ROI and trigger settings')
    settings.frame_interval = 1.0 / args.fps
    settings.burst_interval = settings.frame_interval

    decisions = []

    class ReplayWorker(GateWorker):
        def handle_trigger(self, first_frame):
            result = super().handle_trigger(first_frame)
            decisions.append(result)
            return result

    clock = VirtualClock()
    worker = ReplayWorker(
        gate, config, settings, api, make_controller(gate),
        source_factory=lambda camera: DirectorySource(args.directory),
        clock=clock, sleep=clock.sleep,
        allow_actuation=_may_actuate(gate, args.allow_real_controller),
    )
    worker.run()
    summary = [
        {k: d.get(k) for k in ('decision', 'reason', 'plate', 'confidence', 'actuate', 'event_id')} if d else None
        for d in decisions
    ]
    print(json.dumps({'triggers': len(decisions), 'decisions': summary}, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='gate_agent', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--log-level', default='INFO')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('run', help='Run the agent service')

    decide = sub.add_parser('decide', help='Send photos as one burst and print the decision')
    decide.add_argument('--gate', type=int, required=True)
    decide.add_argument('--roi', action='store_true', help="Crop photos to the gate camera's ROI first")
    decide.add_argument('--allow-real-controller', action='store_true')
    decide.add_argument('images', nargs='+')

    replay = sub.add_parser('replay', help='Replay a directory of frames through the trigger')
    replay.add_argument('--gate', type=int, required=True)
    replay.add_argument('--fps', type=float, default=2.5)
    replay.add_argument('--allow-real-controller', action='store_true')
    replay.add_argument('directory')

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    settings = AgentSettings.from_env()
    handlers = {'run': cmd_run, 'decide': cmd_decide, 'replay': cmd_replay}
    return handlers[args.command](settings, args)


if __name__ == '__main__':
    sys.exit(main())
