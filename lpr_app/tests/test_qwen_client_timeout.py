"""A hung model server must not hold a recognition thread for half an hour."""

from unittest import mock

from django.test import SimpleTestCase, override_settings

from lpr_app.services import qwen_client


@override_settings(QWEN_API_KEY='k', QWEN_BASE_URL='http://model:11434/v1', QWEN_MODEL='m')
class QwenRequestTimeoutTest(SimpleTestCase):
    def test_default_timeout_and_no_retries(self):
        with mock.patch.object(qwen_client, 'OpenAI') as openai:
            qwen_client.QwenVLClient()
        kwargs = openai.call_args.kwargs
        # The SDK's own defaults (600 s, 2 retries) held every gate thread for 30 minutes
        self.assertEqual(kwargs['timeout'], 120.0)
        self.assertEqual(kwargs['max_retries'], 0)

    @override_settings(QWEN_REQUEST_TIMEOUT=45.0, QWEN_MAX_RETRIES=1)
    def test_configurable(self):
        with mock.patch.object(qwen_client, 'OpenAI') as openai:
            qwen_client.QwenVLClient()
        self.assertEqual((openai.call_args.kwargs['timeout'], openai.call_args.kwargs['max_retries']), (45.0, 1))
