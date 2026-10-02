"""Gate controller firmware updates over WiFi: uploads, jobs, the download link and heartbeats."""

import hashlib
import json
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from lpr_app.models import ControllerFirmware, ControllerJob, GateConfigChange, GateDevice
from lpr_app.services import controller_jobs, firmware

from .test_controller_jobs import AGENT, SETTINGS, jobs_url
from .test_gate_decision import MediaDirMixin
from .test_gate_signing import signed

User = get_user_model()
HEARTBEAT = '/api/v1/gate/heartbeat/'


def app_image(version='rf-new', board='esp32dev', chip_id=0, size=4096):
    """Enough of an ESP32 app image: magic, chip id, and the firmware's own tag."""
    head = bytearray(24)
    head[0] = 0xE9
    head[12:14] = chip_id.to_bytes(2, 'little')
    tag = f'GATEFW:{version}:{board}:END'.encode()
    body = bytes(head) + b'\x00' * 64 + tag
    return body + b'\x5a' * (size - len(body))


class InspectTest(TestCase):
    def test_reads_version_board_and_chip(self):
        data = app_image()
        info = firmware.inspect(data)
        self.assertEqual(info, {'version': 'rf-new', 'board': 'esp32dev', 'chip': 'esp32', 'size': 4096,
                                'sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual(firmware.inspect(app_image(board='esp32s3', chip_id=9))['chip'], 'esp32s3')

    def test_refuses_what_is_not_a_gate_controller_app_image(self):
        merged = b'\xff' * 0x10000 + app_image()
        cases = {
            'MERGED_IMAGE': merged,
            'NOT_FIRMWARE': b'PK' + b'\x00' * 4000,
            'NOT_GATE_FIRMWARE': b'\xe9' + b'\x00' * 4000,
            'CHIP_MISMATCH': app_image(board='esp32s3', chip_id=0),
            'TOO_LARGE': app_image(size=firmware.MAX_APP_BYTES + 1),
        }
        for code, data in cases.items():
            with self.assertRaises(firmware.FirmwareRefused, msg=code) as ctx:
                firmware.inspect(data)
            self.assertEqual(ctx.exception.code, code)


@override_settings(**SETTINGS)
class FirmwareUpdateTest(MediaDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user('installer', password='pw')
        self.admin.groups.add(Group.objects.get(name='gate_admin'))
        self.client.force_login(self.admin)
        self.gate = GateDevice(name='West', controller_type='esp32_rf', firmware_version='rf-old',
                               controller_health={'board': 'esp32dev'})
        self.gate.set_controller_token('dev-tok')
        self.gate.save()
        self.nonce = int(time.time() * 1000)

    def upload(self, data=None, name='gate-controller-esp32dev-app.bin'):
        return self.client.post('/api/v1/gate/firmware/', {
            'file': SimpleUploadedFile(name, data or app_image()), 'notes': 'test build'})

    def queue(self, image, gate=None):
        return self.client.post(jobs_url(gate or self.gate), data=json.dumps({'kind': 'update', 'firmware_id': image.id}),
                                content_type='application/json')

    def beat(self, **fields):
        self.nonce += 1
        body = json.dumps({'gate_id': self.gate.id, **fields}).encode()
        return Client(enforce_csrf_checks=True).post(
            HEARTBEAT, data=body, content_type='application/json',
            **signed('dev-tok', 'POST', HEARTBEAT, body, nonce=self.nonce))

    def started(self):
        image = ControllerFirmware.objects.get(pk=self.upload().json()['id'])
        job = ControllerJob.objects.get(pk=self.queue(image).json()['id'])
        jobs = self.client.get('/api/v1/gate/agent-jobs/', **AGENT).json()['jobs']
        self.client.post(f'/api/v1/gate/controller-jobs/{job.id}/result/', data=json.dumps(
            {'sent': True, 'result': 'downloading'}), content_type='application/json', **AGENT)
        job.refresh_from_db()
        return image, job, jobs

    def test_upload_list_and_delete(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.content)
        item = response.json()
        self.assertEqual((item['version'], item['board'], item['chip'], item['uploaded_by'], item['notes']),
                         ('rf-new', 'esp32dev', 'esp32', 'installer', 'test build'))
        duplicate = self.upload()
        self.assertEqual((duplicate.status_code, duplicate.json()['error_code']), (400, 'DUPLICATE'))
        self.assertEqual(self.upload(data=b'not firmware' * 100).json()['error_code'], 'NOT_FIRMWARE')
        self.assertEqual(self.client.post('/api/v1/gate/firmware/', {}).json()['error_code'], 'NO_FILE')
        self.assertEqual(len(self.client.get('/api/v1/gate/firmware/').json()['results']), 1)
        self.assertEqual(self.client.delete(f"/api/v1/gate/firmware/{item['id']}/").status_code, 200)
        self.assertFalse(ControllerFirmware.objects.exists())
        self.assertEqual(self.client.delete(f"/api/v1/gate/firmware/{item['id']}/").status_code, 404)

    def test_operators_cannot_upload_or_update(self):
        operator = User.objects.create_user('guard', password='pw')
        operator.groups.add(Group.objects.get(name='gate_operator'))
        self.client.force_login(operator)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.get('/api/v1/gate/firmware/').status_code, 403)

    def test_refused_updates(self):
        image = ControllerFirmware.objects.get(pk=self.upload().json()['id'])
        relay = GateDevice.objects.create(name='Relay', controller_type='esp32')
        self.assertEqual(self.queue(image, relay).json()['error_code'], 'NOT_SUPPORTED')
        response = self.client.post(jobs_url(self.gate), data=json.dumps({'kind': 'update', 'firmware_id': 999}),
                                    content_type='application/json')
        self.assertEqual(response.json()['error_code'], 'INVALID_FIRMWARE')
        s3 = ControllerFirmware.objects.get(pk=self.upload(app_image('rf-s3', 'esp32s3', 9)).json()['id'])
        self.assertEqual(self.queue(s3).json()['error_code'], 'WRONG_BOARD')
        self.gate.firmware_version = 'rf-new'
        self.gate.save()
        self.assertEqual(self.queue(image).json()['error_code'], 'SAME_VERSION')
        self.gate.firmware_version = 'rf-old'
        self.gate.save()
        self.assertEqual(self.queue(image).status_code, 201)
        response = self.queue(image)
        self.assertEqual((response.status_code, response.json()['error_code']), (409, 'BUSY'))
        # An image an update is using cannot be deleted under it
        self.assertEqual(self.client.delete(f'/api/v1/gate/firmware/{image.id}/').status_code, 409)

    def test_agent_gets_a_one_time_download_path(self):
        image, job, jobs = self.started()
        self.assertEqual(jobs[0]['firmware'], {
            'path': f'/api/v1/gate/firmware/download/{job.token}/', 'sha256': image.sha256,
            'size': image.size, 'version': 'rf-new'})
        self.assertEqual((job.state, job.result), ('running', 'downloading'))
        download = Client().get(jobs[0]['firmware']['path'])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['Content-Length'], str(image.size))
        self.assertEqual(hashlib.sha256(b''.join(download.streaming_content)).hexdigest(), image.sha256)
        self.assertEqual(Client().get('/api/v1/gate/firmware/download/nope/').status_code, 404)

    def test_heartbeats_carry_the_update_through_to_done(self):
        image, job, jobs = self.started()
        self.beat(firmware_version='rf-old', board='esp32dev',
                  update={'state': 'downloading', 'job': job.id, 'progress': 40, 'version': 'rf-new'})
        job.refresh_from_db()
        self.assertEqual((job.state, job.result, job.detail['progress']), ('running', 'downloading', 40))
        listing = self.client.get(jobs_url(self.gate)).json()
        self.assertEqual((listing['update_supported'], listing['board'], listing['firmware_version']),
                         (True, 'esp32dev', 'rf-old'))
        self.assertEqual(listing['update']['progress'], 40)
        # Restarted into the new firmware, on probation, then confirmed
        self.beat(firmware_version='rf-new', update={'state': 'verifying', 'job': job.id, 'version': 'rf-new'})
        job.refresh_from_db()
        self.assertEqual(job.state, 'running')
        self.beat(firmware_version='rf-new', update={'state': 'done', 'job': job.id, 'version': 'rf-new'})
        job.refresh_from_db()
        self.assertEqual((job.state, job.result, job.token), ('done', 'done', ''))
        change = GateConfigChange.objects.filter(object_type='gatedevice', object_id=self.gate.id).first()
        self.assertEqual(change.changes, {'firmware_version': {'old': 'rf-old', 'new': 'rf-new'}})
        # The link died with the job
        self.assertEqual(Client().get(jobs[0]['firmware']['path']).status_code, 404)

    def test_a_rollback_or_a_failure_fails_the_job(self):
        image, job, jobs = self.started()
        self.beat(firmware_version='rf-old', update={'state': 'rolled_back', 'job': job.id, 'error': 'rolled_back'})
        job.refresh_from_db()
        self.assertEqual((job.state, job.result), ('failed', 'rolled_back'))

        self.gate.refresh_from_db()
        second = ControllerJob.objects.get(pk=self.queue(image).json()['id'])
        controller_jobs.claim()
        self.beat(firmware_version='rf-old', update={'state': 'failed', 'job': second.id, 'error': 'sha256_mismatch'})
        second.refresh_from_db()
        self.assertEqual((second.state, second.result), ('failed', 'sha256_mismatch'))

    def test_done_while_running_another_version_is_not_believed(self):
        image, job, jobs = self.started()
        self.beat(firmware_version='rf-other', update={'state': 'done', 'job': job.id})
        job.refresh_from_db()
        self.assertEqual((job.state, job.result), ('failed', 'version_mismatch'))

    def test_an_update_that_never_reports_expires(self):
        image, job, jobs = self.started()
        controller_jobs.expire_stale(timezone.now() + timedelta(seconds=controller_jobs.UPDATE_TIMEOUT_SECONDS - 30))
        job.refresh_from_db()
        self.assertEqual(job.state, 'running')
        controller_jobs.expire_stale(timezone.now() + timedelta(seconds=controller_jobs.UPDATE_TIMEOUT_SECONDS + 5))
        job.refresh_from_db()
        self.assertEqual((job.state, job.token), ('failed', ''))

    def test_heartbeat_update_fields_are_type_checked(self):
        cleaned = controller_jobs.clean_heartbeat({
            'update': {'state': 'downloading', 'job': 'x', 'progress': 400, 'error': 7, 'version': 'v' * 100},
            'board': 'esp32 dev',
        })
        self.assertEqual(cleaned, {'update': {'state': 'downloading', 'version': 'v' * 64}})
        self.assertEqual(controller_jobs.clean_heartbeat({'update': {'state': 'hacked'}}), {})
