"""The manual upload tool is for signed-in people unless it is deliberately public."""

import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from ..models import UploadedImage


def jpeg(name='car.jpg'):
    buffer = io.BytesIO()
    Image.new('RGB', (40, 30), (120, 120, 120)).save(buffer, format='JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


@override_settings(PUBLIC_UPLOAD_ENABLED=False, RATE_LIMIT_ENABLE=False)
class UploadNeedsALoginTest(TestCase):
    def setUp(self):
        self.image = UploadedImage.objects.create(original_image=jpeg(), filename='car.jpg')
        self.user = User.objects.create_user('someone', password='pw')

    def urls(self):
        return [
            '/api/v1/images/',
            f'/api/v1/images/{self.image.pk}/',
            f'/api/v1/download/{self.image.pk}/original/',
            f'/download/{self.image.pk}/original/',
        ]

    def test_anonymous_visitors_are_refused(self):
        for url in self.urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403, url)

    def test_uploading_is_refused(self):
        response = self.client.post('/api/v1/ocr/', {'image': jpeg()})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error_code'], 'FORBIDDEN')

    def test_any_signed_in_user_may_use_it(self):
        # No gate role needed: this is the manual tool, not the gate itself
        self.client.force_login(self.user)
        for url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_the_config_endpoint_tells_the_spa(self):
        self.assertFalse(self.client.get('/api/v1/config/').json()['public_upload'])

    def test_gate_endpoints_are_unaffected(self):
        # Their own roles and tokens already guard them; this must not double up
        self.assertEqual(self.client.get('/api/v1/gate/status/').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/health-light/').status_code, 200)


@override_settings(PUBLIC_UPLOAD_ENABLED=True, RATE_LIMIT_ENABLE=False)
class PublicUploadTest(TestCase):
    def setUp(self):
        self.image = UploadedImage.objects.create(original_image=jpeg(), filename='car.jpg')

    def test_anonymous_visitors_may_browse(self):
        self.assertEqual(self.client.get('/api/v1/images/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/images/{self.image.pk}/').status_code, 200)

    def test_the_config_endpoint_tells_the_spa(self):
        self.assertTrue(self.client.get('/api/v1/config/').json()['public_upload'])
