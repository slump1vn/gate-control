from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from lpr_app.models import Vehicle
from lpr_app.services.plate_matcher import match, find_near_miss
from lpr_app.utils.plates import clean_plate, normalize_plate, within_one_edit


class NormalizePlateTest(SimpleTestCase):
    def test_separator_and_case_variants_normalise_identically(self):
        for raw in ('30A-123.45', '30a 12345', '30A12345', ' 30A–123·45 '):
            self.assertEqual(normalize_plate(raw), '30A12345', raw)

    def test_confusable_characters_corrected_positionally(self):
        self.assertEqual(normalize_plate('3OA-I2345'), '30A12345')

    def test_confusables_in_serial_digits(self):
        self.assertEqual(normalize_plate('30A-I23.S5'), '30A12355')
        self.assertEqual(normalize_plate('30A-B2Z.4G'), '30A82246')

    def test_series_position_coerced_to_letter(self):
        self.assertEqual(normalize_plate('308-123.45'), '30B12345')
        self.assertEqual(normalize_plate('300-123.45'), '30D12345')

    def test_motorbike_series_digit_preserved(self):
        self.assertEqual(normalize_plate('29X1-234.56'), '29X123456')

    def test_motorbike_old_four_digit_serial(self):
        self.assertEqual(normalize_plate('29-X1 2345'), '29X12345')

    def test_car_four_digit_serial(self):
        self.assertEqual(normalize_plate('30A-1234'), '30A1234')
        self.assertEqual(normalize_plate('30A-O234'), '30A0234')

    def test_two_letter_series_is_consistent(self):
        # Position 3 of a two-letter series is coerced the same way for the
        # registry entry and the OCR read, so they still match exactly.
        self.assertEqual(normalize_plate('51LD-123.45'), normalize_plate('51LD12345'))
        self.assertEqual(normalize_plate('51NN-123.45'), '51NN12345')

    def test_vietnamese_d_with_stroke(self):
        self.assertEqual(normalize_plate('30Đ-123.45'), '30D12345')

    def test_unrecognised_layout_passes_through(self):
        self.assertEqual(normalize_plate('abc'), 'ABC')
        self.assertEqual(normalize_plate('NG-123-45X'), 'NG12345X')
        self.assertEqual(normalize_plate('1234567890AB'), '1234567890AB')

    def test_uncoercible_layout_not_partially_coerced(self):
        # 'X' at a province position cannot become a digit -> no coercion at all
        self.assertEqual(normalize_plate('X0A-O2345'), 'X0AO2345')

    def test_empty(self):
        self.assertEqual(normalize_plate(''), '')
        self.assertEqual(normalize_plate(None), '')
        self.assertEqual(clean_plate(None), '')


class WithinOneEditTest(SimpleTestCase):
    def test_substitution(self):
        self.assertTrue(within_one_edit('30A12345', '30A12346'))

    def test_insertion_and_deletion(self):
        self.assertTrue(within_one_edit('30A1234', '30A12345'))
        self.assertTrue(within_one_edit('30A12345', '30A1245'))

    def test_identical_is_not_near(self):
        self.assertFalse(within_one_edit('30A12345', '30A12345'))

    def test_two_edits(self):
        self.assertFalse(within_one_edit('30A12345', '30A12399'))
        self.assertFalse(within_one_edit('30A123', '30A12345'))


class VehicleModelTest(TestCase):
    def test_create_stores_display_and_normalised(self):
        v = Vehicle.objects.create(plate_display='30A-123.45', owner_name='Nguyen Van A')
        self.assertEqual(v.plate_display, '30A-123.45')
        self.assertEqual(v.plate_normalized, '30A12345')
        self.assertTrue(v.is_active)

    def test_duplicate_normalised_plate_rejected_by_clean(self):
        existing = Vehicle.objects.create(plate_display='30A-123.45', owner_name='A')
        dup = Vehicle(plate_display='30a 12345', owner_name='B')
        with self.assertRaises(ValidationError) as ctx:
            dup.full_clean()
        self.assertIn(str(existing.pk), str(ctx.exception))

    def test_duplicate_normalised_plate_rejected_by_database(self):
        Vehicle.objects.create(plate_display='30A-123.45', owner_name='A')
        with self.assertRaises(IntegrityError):
            Vehicle.objects.create(plate_display='3OA12345', owner_name='B')

    def test_editing_self_is_not_a_conflict(self):
        v = Vehicle.objects.create(plate_display='30A-123.45', owner_name='A')
        v.owner_name = 'A2'
        v.full_clean()

    def test_empty_plate_rejected(self):
        with self.assertRaises(ValidationError):
            Vehicle(plate_display='--', owner_name='A').full_clean()

    def test_validity_window_order(self):
        now = timezone.now()
        v = Vehicle(plate_display='30A12345', owner_name='A', valid_from=now, valid_until=now)
        with self.assertRaises(ValidationError):
            v.full_clean()

    def test_access_status(self):
        now = timezone.now()
        v = Vehicle(plate_display='30A12345', owner_name='A')
        self.assertEqual(v.access_status(now), (True, 'whitelist_hit'))
        v.valid_until = now - timedelta(days=1)
        self.assertEqual(v.access_status(now), (False, 'expired'))
        v.valid_until = None
        v.valid_from = now + timedelta(days=1)
        self.assertEqual(v.access_status(now), (False, 'not_yet_valid'))
        v.is_active = False
        self.assertEqual(v.access_status(now), (False, 'inactive'))


class MatchTest(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.car = Vehicle.objects.create(plate_display='30A-123.45', owner_name='A')

    def test_exact_match_on_ocr_variant(self):
        result = match('3OA-I23.45', self.now)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, 'whitelist_hit')
        self.assertEqual(result.vehicle, self.car)
        self.assertEqual(result.plate, '30A12345')

    def test_expired(self):
        self.car.valid_until = self.now - timedelta(hours=1)
        self.car.save()
        result = match('30A12345', self.now)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, 'expired')
        self.assertEqual(result.vehicle, self.car)

    def test_inactive(self):
        self.car.is_active = False
        self.car.save()
        result = match('30A12345', self.now)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, 'inactive')

    def test_not_registered_with_near_miss_is_not_allowed(self):
        result = match('30A12346', self.now)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, 'not_registered')
        self.assertIsNone(result.vehicle)
        self.assertEqual(result.near_miss, self.car)

    def test_not_registered_without_near_miss(self):
        result = match('51F99999', self.now)
        self.assertEqual(result.reason, 'not_registered')
        self.assertIsNone(result.near_miss)

    def test_ambiguous_near_miss_reports_none(self):
        Vehicle.objects.create(plate_display='30A12347', owner_name='B')
        self.assertIsNone(find_near_miss('30A12346', self.now))

    def test_near_miss_ignores_inactive_and_expired(self):
        self.car.is_active = False
        self.car.save()
        self.assertIsNone(find_near_miss('30A12346', self.now))

    def test_empty_plate(self):
        result = match('', self.now)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, 'no_plate')
