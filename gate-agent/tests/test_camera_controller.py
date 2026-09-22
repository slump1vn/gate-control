import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

import requests
from PIL import Image

from gate_agent.camera import (
    CameraAuthError, CameraError, DirectorySource, EndOfSource, RtspSource, SnapshotSource,
    build_source, crop_roi, encode_jpeg, redact, rtsp_url,
)
from gate_agent.controller import ControllerClient, ControllerError


def jpeg_bytes(size=(64, 48), color=(10, 20, 30)):
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='JPEG')
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status=200, content=b'', headers=None, body=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self._body = body
        self.text = str(body)

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


class SnapshotSourceTest(unittest.TestCase):
    def _source(self, responses, username='admin'):
        session = mock.Mock()
        session.get.side_effect = responses
        return SnapshotSource('192.168.1.64', 80, 'ISAPI/Streaming/channels/101/picture', username, 'pw',
                              session=session), session

    def test_digest_challenge_then_image_and_auth_reused(self):
        source, session = self._source([
            FakeResponse(401, headers={'WWW-Authenticate': 'Digest realm="x"'}),
            FakeResponse(200, jpeg_bytes()),
            FakeResponse(200, jpeg_bytes()),
        ])
        self.assertEqual(source.grab().size, (64, 48))
        self.assertEqual(type(session.get.call_args_list[1].kwargs['auth']).__name__, 'HTTPDigestAuth')
        source.grab()
        self.assertEqual(session.get.call_count, 3)
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertEqual(source.describe(), 'http://192.168.1.64:80/ISAPI/Streaming/channels/101/picture')

    def test_basic_challenge(self):
        source, session = self._source([FakeResponse(401, headers={'WWW-Authenticate': 'Basic'}), FakeResponse(200, jpeg_bytes())])
        source.grab()
        self.assertEqual(type(session.get.call_args.kwargs['auth']).__name__, 'HTTPBasicAuth')

    def test_wrong_password(self):
        source, _ = self._source([FakeResponse(401, headers={'WWW-Authenticate': 'Digest'}), FakeResponse(401)])
        with self.assertRaises(CameraAuthError) as ctx:
            source.grab()
        self.assertEqual(ctx.exception.status, 'auth_failed')
        self.assertIsNone(source.auth)

    def test_errors(self):
        for responses, message in (
            ([FakeResponse(500)], 'HTTP 500'),
            ([FakeResponse(200, b'not an image')], 'not an image'),
            ([requests.ConnectionError()], 'ConnectionError'),
        ):
            source, _ = self._source(responses)
            with self.assertRaises(CameraError) as ctx:
                source.grab()
            self.assertIn(message, str(ctx.exception))
            self.assertEqual(ctx.exception.status, 'unreachable')


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def test_directory_source_in_order_then_end(self):
        for name, color in (('b.jpg', 'blue'), ('a.png', 'red'), ('notes.txt', None)):
            path = os.path.join(self.dir, name)
            if color:
                Image.new('RGB', (8, 8), color).save(path)
            else:
                open(path, 'w').close()
        source = DirectorySource(self.dir)
        self.assertEqual(source.describe(), '2 files')
        self.assertEqual(source.grab().getpixel((0, 0)), (255, 0, 0))
        source.grab()
        with self.assertRaises(EndOfSource):
            source.grab()
        source.close()

    def test_build_source_prefers_snapshot(self):
        cam = {'host': '10.0.0.5', 'http_port': 8080, 'snapshot_path': '/snap.jpg', 'main_stream_path': '/s',
               'prefer_snapshot': True, 'username': 'u', 'password': 'p'}
        self.assertIsInstance(build_source(cam), SnapshotSource)
        cam['prefer_snapshot'] = False
        self.assertIsInstance(build_source(cam), RtspSource)
        cam['main_stream_path'] = ''
        with self.assertRaises(CameraError):
            build_source(cam)
        with self.assertRaises(CameraAuthError):
            build_source({'credential_error': 'password_unavailable'})

    def test_rtsp_url_and_redact(self):
        cam = {'host': '172.87.80.80', 'rtsp_port': 554, 'username': 'admin', 'password': 'p@ss',
               'main_stream_path': 'Streaming/Channels/101', 'sub_stream_path': '/Streaming/Channels/102'}
        url = rtsp_url(cam)
        self.assertEqual(url, 'rtsp://admin:p%40ss@172.87.80.80:554/Streaming/Channels/101')
        self.assertIn('/102', rtsp_url(cam, 'sub'))
        self.assertEqual(redact(url), 'rtsp://admin:***@172.87.80.80:554/Streaming/Channels/101')
        self.assertEqual(RtspSource(url).describe(), redact(url))
        self.assertEqual(rtsp_url({'host': 'h', 'main_stream_path': '/x'}), 'rtsp://h:554/x')

    def test_rtsp_without_opencv(self):
        with mock.patch.dict('sys.modules', {'cv2': None}):
            with self.assertRaises(CameraError) as ctx:
                RtspSource('rtsp://x/').grab()
        self.assertIn('opencv', str(ctx.exception))

    def test_crop_roi(self):
        image = Image.new('RGB', (200, 100))
        self.assertEqual(crop_roi(image, None).size, (200, 100))
        self.assertEqual(crop_roi(image, {'x': 0.1, 'y': 0.5, 'w': 0.5, 'h': 0.5}).size, (100, 50))
        self.assertEqual(crop_roi(image, {'x': 0.9, 'y': 0.9, 'w': 0.0, 'h': 0.0}).size, (200, 100))

    def test_encode_jpeg_steps_down_only_when_needed(self):
        noisy = Image.effect_noise((800, 600), 100).convert('RGB')
        data, quality = encode_jpeg(noisy, 10 * 1024 * 1024, quality=90)
        self.assertEqual(quality, 90)
        small_limit = int(len(data) * 0.6)
        data, quality = encode_jpeg(noisy, small_limit, quality=90)
        self.assertLess(quality, 90)
        self.assertLessEqual(len(data), small_limit)
        with self.assertRaises(ValueError):
            encode_jpeg(noisy, 1000, quality=90)


try:
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


@unittest.skipUnless(HAVE_NUMPY, 'numpy not installed')
class RtspFakeCv2Test(unittest.TestCase):
    def test_reads_and_converts_frames(self):
        import numpy as np
        cv2 = mock.Mock()
        capture = mock.Mock()
        capture.isOpened.return_value = True
        capture.read.side_effect = [(True, np.zeros((10, 20, 3), dtype=np.uint8)), (False, None)]
        cv2.VideoCapture.return_value = capture
        cv2.cvtColor.side_effect = lambda frame, code: frame
        with mock.patch.dict('sys.modules', {'cv2': cv2}):
            source = RtspSource('rtsp://x/')
            self.assertEqual(source.grab().size, (20, 10))
            with self.assertRaises(CameraError):
                source.grab()
        capture.release.assert_called_once()

    def test_stream_not_opened(self):
        cv2 = mock.Mock()
        cv2.VideoCapture.return_value.isOpened.return_value = False
        with mock.patch.dict('sys.modules', {'cv2': cv2}):
            with self.assertRaises(CameraError):
                RtspSource('rtsp://x/').grab()


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.session = mock.Mock()
        self.now = [1000.0]
        self.wall = [1_700_000_000.0]
        self.client = ControllerClient(
            'http://lpr-app:8000/api/v1/gate/sim/1', 'tok', session=self.session,
            clock=lambda: self.now[0], wall_clock=lambda: self.wall[0],
        )

    def test_open_sends_contract(self):
        self.session.post.return_value = FakeResponse(200, body={'ok': True, 'result': 'ok', 'arm_state': 'moving'})
        body = self.client.send('open')
        self.assertEqual(body['arm_state'], 'moving')
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], 'http://lpr-app:8000/api/v1/gate/sim/1/open')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer tok')
        self.assertEqual(kwargs['json']['ts'], self.wall[0])
        self.assertEqual(kwargs['json']['nonce'], int(self.wall[0] * 1000))

    def test_nonce_strictly_increases_even_with_same_clock(self):
        self.session.post.return_value = FakeResponse(200, body={'ok': True})
        self.client.send('stop')
        self.client.send('stop')
        nonces = [c.kwargs['json']['nonce'] for c in self.session.post.call_args_list]
        self.assertEqual(nonces[1], nonces[0] + 1)

    def test_local_rate_limit_but_stop_always_allowed(self):
        self.session.post.return_value = FakeResponse(200, body={'ok': True})
        self.client.send('open')
        self.now[0] += 1
        with self.assertRaises(ControllerError) as ctx:
            self.client.send('close')
        self.assertEqual(ctx.exception.status, 429)
        self.client.send('stop')
        self.now[0] += 3
        self.client.send('close')

    def test_refused_command_does_not_start_rate_limit(self):
        self.session.post.side_effect = [FakeResponse(409, body={'error': 'UP/DOWN interlock'}),
                                         FakeResponse(200, body={'ok': True})]
        with self.assertRaises(ControllerError) as ctx:
            self.client.send('open')
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn('interlock', ctx.exception.message)
        self.client.send('open')

    def test_retries_once_on_connection_error(self):
        self.session.post.side_effect = [requests.ConnectionError(), FakeResponse(200, content=b'', body=None)]
        self.assertEqual(self.client.send('open'), {'ok': True})
        self.session.post.side_effect = [requests.Timeout(), requests.Timeout()]
        self.now[0] += 10
        with self.assertRaises(ControllerError) as ctx:
            self.client.send('open')
        self.assertIn('unreachable', ctx.exception.message)

    def test_non_json_error_and_unknown_command(self):
        self.session.post.return_value = FakeResponse(500, body=None)
        with self.assertRaises(ControllerError):
            self.client.send('stop')
        with self.assertRaises(ControllerError):
            self.client.send('explode')

    def test_status(self):
        self.session.get.return_value = FakeResponse(200, body={'arm_state': 'down'})
        self.assertEqual(self.client.status()['arm_state'], 'down')
        self.session.get.return_value = FakeResponse(401, body={})
        with self.assertRaises(ControllerError):
            self.client.status()
        self.session.get.side_effect = requests.ConnectionError()
        with self.assertRaises(ControllerError):
            self.client.status()


if __name__ == '__main__':
    unittest.main()
