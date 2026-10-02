"""
Gate controller firmware images uploaded from the admin UI, for updates over
WiFi (see controller_jobs.create_update and gate-controller/src/ota.cpp).

An upload must be an ESP32 *app* image (CI's gate-controller-<board>-app.bin,
not the -merged.bin flashed at 0x0). The firmware tags itself with
"GATEFW:<version>:<board>:END"; that is how the version and the board are
known without trusting what the uploader typed.
"""

import hashlib
import re

from ..models import ControllerFirmware

IMAGE_MAGIC = 0xE9
# The app slots of the default ESP32 partition table (app0/app1)
MAX_APP_BYTES = 0x140000
CHIPS = {0: 'esp32', 2: 'esp32s2', 5: 'esp32c3', 9: 'esp32s3'}
BOARD_CHIPS = {'esp32dev': 'esp32', 'esp32s3': 'esp32s3'}
TAG = re.compile(rb'GATEFW:([A-Za-z0-9._-]{1,64}):([a-z0-9]{1,16}):END')


class FirmwareRefused(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.message = message
        self.code = code


def inspect(data):
    """What an uploaded image is: {version, board, chip, size, sha256}. Raises FirmwareRefused."""
    if len(data) < 1024 or data[0] != IMAGE_MAGIC:
        if len(data) > 0x10000 and data[0x10000] == IMAGE_MAGIC:
            raise FirmwareRefused('This is a merged image (flashed at 0x0); upload the -app.bin', 'MERGED_IMAGE')
        raise FirmwareRefused('Not an ESP32 app image', 'NOT_FIRMWARE')
    if len(data) > MAX_APP_BYTES:
        raise FirmwareRefused(f'Image is {len(data)} bytes; an app slot holds {MAX_APP_BYTES}', 'TOO_LARGE')
    chip = CHIPS.get(int.from_bytes(data[12:14], 'little'), 'unknown')
    tags = TAG.findall(data)
    if not tags:
        raise FirmwareRefused('Not a gate controller firmware (no GATEFW tag)', 'NOT_GATE_FIRMWARE')
    version, board = (part.decode() for part in tags[0])
    if BOARD_CHIPS.get(board) not in (None, chip):
        raise FirmwareRefused(f'Built for {board} but the image is for {chip}', 'CHIP_MISMATCH')
    return {
        'version': version,
        'board': board,
        'chip': chip,
        'size': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
    }


def create_from_upload(uploaded, user, notes=''):
    data = uploaded.read()
    info = inspect(data)
    existing = ControllerFirmware.objects.filter(sha256=info['sha256']).first()
    if existing:
        raise FirmwareRefused(f'Already uploaded as {existing.version} ({existing.board})', 'DUPLICATE')
    firmware = ControllerFirmware(
        notes=str(notes or '')[:200],
        uploaded_by=user if getattr(user, 'is_authenticated', False) else None,
        **info,
    )
    from django.core.files.base import ContentFile
    firmware.file.save(f"gate-controller-{info['board']}-{info['version']}.bin", ContentFile(data), save=False)
    firmware.save()
    return firmware


def serialize(firmware):
    return {
        'id': firmware.id,
        'version': firmware.version,
        'board': firmware.board,
        'chip': firmware.chip,
        'size': firmware.size,
        'sha256': firmware.sha256,
        'notes': firmware.notes,
        'uploaded_by': firmware.uploaded_by.get_username() if firmware.uploaded_by else None,
        'created_at': firmware.created_at.isoformat() if firmware.created_at else None,
    }
