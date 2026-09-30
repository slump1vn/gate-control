"""Model requests: a timeout, no retries, and the fallback server while the primary fails."""

import time
from types import SimpleNamespace
from unittest import mock

import httpx
import openai
from django.test import SimpleTestCase, override_settings

from lpr_app import metrics
from lpr_app.services import model_router, qwen_client

BASE = dict(QWEN_API_KEY='k', QWEN_BASE_URL='http://ollama:11434/v1', QWEN_MODEL='qwen3-vl:8b',
            QWEN_REQUEST_TIMEOUT=60.0, QWEN_MAX_RETRIES=0, QWEN_FALLBACK_BASE_URL='',
            QWEN_FALLBACK_MODEL='', QWEN_FALLBACK_API_KEY='', QWEN_FALLBACK_REQUEST_TIMEOUT=0.0,
            QWEN_PRIMARY_RETRY_SECONDS=60.0)
WITH_FALLBACK = dict(BASE, QWEN_FALLBACK_BASE_URL='http://llamacpp:8000/v1', QWEN_FALLBACK_MODEL='Qwen3-VL-4B')
REQUEST = httpx.Request('POST', 'http://model/v1/chat/completions')


def answer(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def timeout():
    return openai.APITimeoutError(request=REQUEST)


def status(code):
    return openai.APIStatusError('error', response=httpx.Response(code, request=REQUEST), body=None)


class FakeServers:
    """Stands in for the SDK clients: each endpoint answers from its own script."""

    def __init__(self, primary=(), fallback=()):
        self.script = {'primary': list(primary), 'fallback': list(fallback)}
        self.calls = []

    def client_for(self, endpoint):
        def create(**request):
            self.calls.append((endpoint.name, request['model']))
            outcome = self.script[endpoint.name].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return answer(outcome)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class RouterTestBase(SimpleTestCase):
    def setUp(self):
        model_router.reset()
        self.addCleanup(model_router.reset)

    def run_with(self, servers):
        patcher = mock.patch.object(model_router, 'client_for', side_effect=servers.client_for)
        patcher.start()
        self.addCleanup(patcher.stop)
        return qwen_client.QwenVLClient()


@override_settings(**BASE)
class RequestTimeoutTest(RouterTestBase):
    def test_sdk_client_gets_the_timeout_and_no_retries(self):
        with mock.patch.object(model_router, 'OpenAI') as sdk:
            model_router.client_for(model_router.endpoints()[0])
        kwargs = sdk.call_args.kwargs
        # The SDK's own defaults (600 s, 2 retries) held every gate thread for 30 minutes
        self.assertEqual((kwargs['timeout'], kwargs['max_retries']), (60.0, 0))
        self.assertEqual(kwargs['base_url'], 'http://ollama:11434/v1')

    def test_clients_are_reused(self):
        with mock.patch.object(model_router, 'OpenAI') as sdk:
            endpoint = model_router.endpoints()[0]
            self.assertIs(model_router.client_for(endpoint), model_router.client_for(endpoint))
        self.assertEqual(sdk.call_count, 1)

    def test_without_a_fallback_a_failure_is_just_a_failure(self):
        servers = FakeServers(primary=[timeout(), 'second try'])
        client = self.run_with(servers)
        self.assertIsNone(client.analyze_image('aW1n', 'read'))
        # No fallback: the primary is never put aside
        self.assertEqual(client.analyze_image('aW1n', 'read'), 'second try')
        self.assertEqual(servers.calls, [('primary', 'qwen3-vl:8b'), ('primary', 'qwen3-vl:8b')])


@override_settings(**WITH_FALLBACK)
class FallbackTest(RouterTestBase):
    def test_primary_timeout_goes_to_the_fallback_and_stays_there(self):
        servers = FakeServers(primary=[timeout()], fallback=['30A12345', '30A12346'])
        client = self.run_with(servers)
        self.assertEqual(client.analyze_image('aW1n', 'read'), '30A12345')
        self.assertFalse(model_router.primary_available())
        self.assertEqual(metrics.MODEL_PRIMARY_AVAILABLE._value.get(), 0)
        # Straight to the fallback now: no waiting out the primary's timeout again
        self.assertEqual(client.analyze_image('aW1n', 'read'), '30A12346')
        self.assertEqual(servers.calls, [('primary', 'qwen3-vl:8b'), ('fallback', 'Qwen3-VL-4B'),
                                         ('fallback', 'Qwen3-VL-4B')])

    def test_primary_is_tried_again_after_the_retry_period(self):
        servers = FakeServers(primary=[status(502), 'back'], fallback=['from fallback'])
        client = self.run_with(servers)
        client.analyze_image('aW1n', 'read')
        with mock.patch.object(model_router.time, 'monotonic', return_value=time.monotonic() + 61):
            self.assertEqual(client.analyze_image('aW1n', 'read'), 'back')
        self.assertTrue(model_router.primary_available())
        self.assertEqual(metrics.MODEL_PRIMARY_AVAILABLE._value.get(), 1)

    def test_connection_errors_and_5xx_fall_back(self):
        for failure in (openai.APIConnectionError(request=REQUEST), status(500), status(503)):
            model_router.reset()
            servers = FakeServers(primary=[failure], fallback=['ok'])
            self.assertEqual(self.run_with(servers).analyze_image('aW1n', 'read'), 'ok', failure)

    def test_a_bad_request_does_not_fall_back(self):
        # A 4xx would be just as wrong on the other server
        servers = FakeServers(primary=[status(400)], fallback=['never'])
        self.assertIsNone(self.run_with(servers).analyze_image('aW1n', 'read'))
        self.assertEqual(servers.calls, [('primary', 'qwen3-vl:8b')])
        self.assertTrue(model_router.primary_available())

    def test_both_down(self):
        servers = FakeServers(primary=[timeout()], fallback=[timeout()])
        self.assertIsNone(self.run_with(servers).analyze_image('aW1n', 'read'))

    def test_fallback_defaults(self):
        with override_settings(QWEN_FALLBACK_MODEL='', QWEN_FALLBACK_API_KEY='', QWEN_API_KEY='k'):
            fallback = model_router.endpoints()[1]
        self.assertEqual((fallback.model, fallback.api_key, fallback.timeout), ('qwen3-vl:8b', 'k', 60.0))
        with override_settings(QWEN_FALLBACK_REQUEST_TIMEOUT=90.0, QWEN_FALLBACK_API_KEY='llama'):
            fallback = model_router.endpoints()[1]
        self.assertEqual((fallback.api_key, fallback.timeout), ('llama', 90.0))

    def test_failover_is_logged_once(self):
        servers = FakeServers(primary=[timeout()], fallback=['a', 'b'])
        client = self.run_with(servers)
        with self.assertLogs('lpr_app.services.model_router', 'WARNING') as logs:
            client.analyze_image('aW1n', 'read')
            client.analyze_image('aW1n', 'read')
        self.assertEqual(len(logs.output), 1)
        self.assertIn('using the fallback', logs.output[0])
