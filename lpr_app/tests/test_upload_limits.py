import json
import os
import unittest

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, RequestFactory, override_settings

from lpr_app.services.api_service import ApiService

TWO_MB = 2 * 1024 * 1024


def _jpeg_of_size(size):
    return SimpleUploadedFile('frame.jpg', b'\xff' * size, content_type='image/jpeg')


class UploadLimitDefaultTest(TestCase):
    @unittest.skipIf('UPLOAD_FILE_MAX_SIZE' in os.environ, 'overridden by environment')
    def test_default_is_2mb(self):
        self.assertEqual(settings.UPLOAD_FILE_MAX_SIZE, TWO_MB)

    def test_memory_threshold_follows_upload_limit(self):
        self.assertEqual(settings.FILE_UPLOAD_MAX_MEMORY_SIZE, settings.UPLOAD_FILE_MAX_SIZE)


@override_settings(UPLOAD_FILE_MAX_SIZE=TWO_MB)
class UploadLimitValidationTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _validate(self, size):
        request = self.factory.post('/api/v1/ocr/', {'image': _jpeg_of_size(size)})
        return ApiService.validate_api_request(request)

    def test_file_under_limit_accepted(self):
        is_valid, error = self._validate(int(1.9 * 1024 * 1024))
        self.assertTrue(is_valid)
        self.assertIsNone(error)

    def test_file_at_limit_accepted(self):
        is_valid, _ = self._validate(TWO_MB)
        self.assertTrue(is_valid)

    def test_file_over_limit_rejected(self):
        is_valid, error = self._validate(int(2.1 * 1024 * 1024))
        self.assertFalse(is_valid)
        self.assertEqual(error.status_code, 400)
        body = json.loads(error.content)
        self.assertEqual(body['error_code'], 'FILE_TOO_LARGE')
        self.assertIn('2.0MB', body['error'])

    @override_settings(UPLOAD_FILE_MAX_SIZE=10 * 1024 * 1024)
    def test_limit_is_overridable(self):
        is_valid, _ = self._validate(int(5 * 1024 * 1024))
        self.assertTrue(is_valid)


@override_settings(UPLOAD_FILE_MAX_SIZE=TWO_MB)
class UploadLimitConfigEndpointTest(TestCase):
    def test_config_reports_limit(self):
        response = self.client.get('/api/v1/config/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['max_upload_bytes'], TWO_MB)
