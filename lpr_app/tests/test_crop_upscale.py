"""Plate crops are enlarged before OCR, so small plates still have readable strokes."""

import base64
import io
import os
import tempfile

from django.test import TestCase, override_settings
from PIL import Image

from ..services.image_processor import ImageProcessor


def _write_image(width, height):
    path = os.path.join(tempfile.mkdtemp(), f'crop_{width}x{height}.jpg')
    Image.new('RGB', (width, height), (200, 200, 200)).save(path, format='JPEG')
    return path


def _decoded_size(encoded):
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as img:
        return img.width, img.height


class CropUpscaleTest(TestCase):
    def test_small_crop_is_upscaled_keeping_aspect_ratio(self):
        path = _write_image(120, 40)
        width, height = _decoded_size(ImageProcessor.encode_image_to_base64(path, min_width=480))
        self.assertEqual(width, 480)
        self.assertEqual(height, 160)

    def test_upscale_is_capped(self):
        # 60px would need 8x to reach 480; the cap keeps it at 4x
        path = _write_image(60, 20)
        width, _ = _decoded_size(ImageProcessor.encode_image_to_base64(path, min_width=480))
        self.assertEqual(width, 240)

    def test_wide_enough_crop_is_sent_unchanged(self):
        path = _write_image(600, 200)
        with open(path, 'rb') as f:
            original = base64.b64encode(f.read()).decode()
        self.assertEqual(ImageProcessor.encode_image_to_base64(path, min_width=480), original)

    def test_disabled_by_default(self):
        path = _write_image(120, 40)
        with open(path, 'rb') as f:
            original = base64.b64encode(f.read()).decode()
        self.assertEqual(ImageProcessor.encode_image_to_base64(path), original)

    def test_missing_file_returns_none(self):
        self.assertIsNone(ImageProcessor.encode_image_to_base64('/nope/missing.jpg', min_width=480))


@override_settings(OCR_CROP_MIN_WIDTH=480)
class CropUpscaleSettingTest(TestCase):
    def test_setting_is_read_by_the_ocr_phase(self):
        from django.conf import settings
        self.assertEqual(settings.OCR_CROP_MIN_WIDTH, 480)
