import os
import uuid

from django.db import models
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.validators import MaxValueValidator, MinValueValidator
from django.conf import settings
from django.utils import timezone

from .utils.plates import normalize_plate
from .utils.secrets import decrypt_secret, encrypt_secret


def _guid_filename(filename):
    return f'{uuid.uuid4().hex[:8]}_{filename}'


def upload_to_uploads(instance, filename):
    from datetime import datetime
    now = datetime.now()
    return f'uploads/{now.year}/{now.month:02d}/{now.day:02d}/{_guid_filename(filename)}'


def upload_to_processed(instance, filename):
    from datetime import datetime
    now = datetime.now()
    return f'processed/{now.year}/{now.month:02d}/{now.day:02d}/{_guid_filename(filename)}'


class UploadedImage(models.Model):
    """Model to track uploaded images and their processing results"""
    
    id = models.AutoField(primary_key=True)
    original_image = models.ImageField(
        upload_to=upload_to_uploads,
        verbose_name="Original Image"
    )
    processed_image = models.ImageField(
        upload_to=upload_to_processed,
        null=True,
        blank=True,
        verbose_name="Processed Image"
    )
    upload_timestamp = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Upload Time"
    )
    processing_timestamp = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Processing Time"
    )
    api_response = models.JSONField(
        null=True,
        blank=True,
        verbose_name="API Response"
    )
    filename = models.CharField(
        max_length=255,
        verbose_name="Filename"
    )
    file_size = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="File Size (bytes)"
    )
    processing_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        ],
        default='pending',
        verbose_name="Processing Status"
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        verbose_name="Error Message"
    )
    retry_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Retry Count"
    )
    max_retries = models.PositiveIntegerField(
        default=2,
        verbose_name="Max Retries"
    )
    source = models.CharField(
        max_length=10,
        choices=[
            ('upload', 'Upload'),
            ('gate', 'Gate Camera'),
        ],
        default='upload',
        db_index=True,
        verbose_name="Source",
        help_text="Gate camera frames are excluded from the public image endpoints.",
    )
    
    class Meta:
        verbose_name = "Uploaded Image"
        verbose_name_plural = "Uploaded Images"
        ordering = ['-upload_timestamp']
    
    def __str__(self):
        return f"{self.filename} - {self.upload_timestamp.strftime('%Y-%m-%d %H:%M')}"
    
    def save(self, *args, **kwargs):
        if not self.pk:
            if self.original_image:
                self.filename = os.path.basename(self.original_image.name)
                if hasattr(self.original_image, 'size'):
                    self.file_size = self.original_image.size
            self.max_retries = getattr(settings, 'MAX_RETRIES', 2)
        
        super().save(*args, **kwargs)
    
    @property
    def original_image_url(self):
        """Get URL for original image"""
        if self.original_image:
            return self.original_image.url
        return None
    
    @property
    def processed_image_url(self):
        """Get URL for processed image"""
        if self.processed_image:
            return self.processed_image.url
        return None
    
    @property
    def file_size_mb(self):
        """Get file size in MB"""
        if self.file_size:
            return round(self.file_size / (1024 * 1024), 2)
        return None
    
    def get_detection_results(self):
        """Parse and return detection results from API response"""
        if not self.api_response:
            return None
        
        import json
        try:
            # Parse the JSON response to extract detection results
            if isinstance(self.api_response, str):
                response_data = json.loads(self.api_response)
            else:
                response_data = self.api_response
            
            return response_data
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
    
    def get_plate_count(self):
        """Get the number of license plates detected"""
        results = self.get_detection_results()
        if results and 'detections' in results:
            return len(results['detections'])
        return 0
    
    def get_total_ocr_count(self):
        """Get the total number of OCR detections"""
        results = self.get_detection_results()
        if results and 'detections' in results:
            total = 0
            detections = results['detections']
            # Handle both list and dictionary formats
            if isinstance(detections, list):
                # New API format: detections is a list
                for detection in detections:
                    if 'ocr' in detection:
                        total += len(detection['ocr'])
            elif isinstance(detections, dict):
                # Old API format: detections is a dictionary
                for detection in detections.values():
                    if 'ocr' in detection:
                        total += len(detection['ocr'])
            return total
        return 0
    
    def get_first_ocr_text(self):
        """Get the first OCR text from the detection results"""
        results = self.get_detection_results()
        if results and 'detections' in results:
            detections = results['detections']
            # Handle both list and dictionary formats
            if isinstance(detections, list):
                # New API format: detections is a list
                for detection in detections:
                    if 'ocr' in detection and detection['ocr']:
                        ocr_item = detection['ocr'][0]
                        # Handle both new and old OCR formats
                        if isinstance(ocr_item, dict):
                            if 'text' in ocr_item:
                                # New format: {'text': 'value', 'confidence': 0.95, 'coordinates': {...}}
                                return ocr_item['text']
                            else:
                                # Old format: {'text_value': {'confidence': 0.95, 'coordinates': {...}}}
                                # The text is the key itself
                                return list(ocr_item.keys())[0] if ocr_item else None
            elif isinstance(detections, dict):
                # Old API format: detections is a dictionary
                for detection in detections.values():
                    if 'ocr' in detection and detection['ocr']:
                        ocr_item = detection['ocr'][0]
                        # Handle both new and old OCR formats
                        if isinstance(ocr_item, dict):
                            if 'text' in ocr_item:
                                # New format: {'text': 'value', 'confidence': 0.95, 'coordinates': {...}}
                                return ocr_item['text']
                            else:
                                # Old format: {'text_value': {'confidence': 0.95, 'coordinates': {...}}}
                                # The text is the key itself
                                return list(ocr_item.keys())[0] if ocr_item else None
        return None


class ProcessingLog(models.Model):
    """Model to log processing attempts and errors"""
    
    uploaded_image = models.ForeignKey(
        UploadedImage,
        on_delete=models.CASCADE,
        related_name='processing_logs'
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ('started', 'Started'),
            ('api_call', 'API Call'),
            ('success', 'Success'),
            ('error', 'Error'),
        ]
    )
    message = models.TextField()
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Processing Log"
        verbose_name_plural = "Processing Logs"
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.uploaded_image.filename} - {self.status} - {self.timestamp}"


# ---------------------------------------------------------------------------
# Gate automation
# ---------------------------------------------------------------------------

_port_validators = [MinValueValidator(1), MaxValueValidator(65535)]
_unit_validators = [MinValueValidator(0.0), MaxValueValidator(1.0)]


class Vehicle(models.Model):
    """A vehicle authorised to pass the gate."""

    VEHICLE_TYPES = [
        ('car', 'Car'),
        ('motorbike', 'Motorbike'),
        ('other', 'Other'),
    ]

    plate_normalized = models.CharField(max_length=20, unique=True, editable=False)
    plate_display = models.CharField(max_length=32, verbose_name="Plate Number")
    owner_name = models.CharField(max_length=255)
    owner_phone = models.CharField(max_length=32, blank=True)
    department = models.CharField(max_length=255, blank=True)
    vehicle_type = models.CharField(max_length=16, choices=VEHICLE_TYPES, default='car')
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vehicle"
        verbose_name_plural = "Vehicles"
        ordering = ['plate_normalized']

    def __str__(self):
        return f"{self.plate_display} ({self.owner_name})"

    def clean(self):
        normalized = normalize_plate(self.plate_display)
        if not normalized:
            raise ValidationError({'plate_display': 'Plate number is required.'})
        conflict = Vehicle.objects.filter(plate_normalized=normalized).exclude(pk=self.pk).first()
        if conflict:
            raise ValidationError({
                'plate_display': f'Plate matches existing vehicle {conflict.plate_display} '
                                 f'(id {conflict.pk}) after normalisation.'
            })
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValidationError({'valid_until': 'Must be after valid_from.'})

    def save(self, *args, **kwargs):
        self.plate_normalized = normalize_plate(self.plate_display)
        super().save(*args, **kwargs)

    def access_status(self, at=None):
        """Return (allowed, reason) for this vehicle at the given time."""
        at = at or timezone.now()
        if not self.is_active:
            return False, 'inactive'
        if self.valid_from and at < self.valid_from:
            return False, 'not_yet_valid'
        if self.valid_until and at >= self.valid_until:
            return False, 'expired'
        return True, 'whitelist_hit'


class Camera(models.Model):
    """An IP camera watching a gate lane, managed from the admin UI."""

    VENDORS = [
        ('hikvision', 'Hikvision'),
        ('dahua', 'Dahua'),
        ('generic', 'Generic'),
    ]

    name = models.CharField(max_length=100)
    is_enabled = models.BooleanField(default=True)
    host = models.CharField(max_length=255)
    rtsp_port = models.PositiveIntegerField(default=554, validators=_port_validators)
    http_port = models.PositiveIntegerField(default=80, validators=_port_validators)
    username = models.CharField(max_length=100, blank=True)
    password_encrypted = models.TextField(blank=True, editable=False)
    vendor = models.CharField(max_length=16, choices=VENDORS, default='generic')
    main_stream_path = models.CharField(max_length=255, blank=True)
    sub_stream_path = models.CharField(max_length=255, blank=True)
    snapshot_path = models.CharField(max_length=255, blank=True)
    live_snapshot_path = models.CharField(
        max_length=255, blank=True,
        help_text='Snapshot path for the live monitoring view; usually the sub-stream, '
                  'which is cheaper for the camera. Empty uses the path above.',
    )
    prefer_snapshot = models.BooleanField(default=True)
    roi_x = models.FloatField(null=True, blank=True, validators=_unit_validators)
    roi_y = models.FloatField(null=True, blank=True, validators=_unit_validators)
    roi_w = models.FloatField(null=True, blank=True, validators=_unit_validators)
    roi_h = models.FloatField(null=True, blank=True, validators=_unit_validators)
    motion_threshold = models.FloatField(default=0.02, validators=_unit_validators)
    settle_ms = models.PositiveIntegerField(default=800)
    cooldown_s = models.PositiveIntegerField(default=5)
    config_version = models.PositiveIntegerField(default=1, editable=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+', editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_test_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_test_ok = models.BooleanField(null=True, blank=True, editable=False)
    last_test_error = models.TextField(blank=True, editable=False)
    agent_status = models.CharField(max_length=20, blank=True, editable=False)
    agent_trigger = models.JSONField(
        default=dict, blank=True, editable=False,
        help_text='What the agent last measured on this camera: frame rate, and the motion and '
                  'presence scores against their thresholds. Shows why a vehicle did or did not '
                  'start a recognition burst.',
    )
    agent_status_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Camera"
        verbose_name_plural = "Cameras"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.host})"

    @property
    def password_set(self):
        return bool(self.password_encrypted)

    def set_password(self, raw):
        self.password_encrypted = encrypt_secret(raw) if raw else ''

    def get_password(self):
        return decrypt_secret(self.password_encrypted)

    @property
    def roi(self):
        values = (self.roi_x, self.roi_y, self.roi_w, self.roi_h)
        if any(v is None for v in values):
            return None
        return dict(zip(('x', 'y', 'w', 'h'), values))

    def clean(self):
        roi = (self.roi_x, self.roi_y, self.roi_w, self.roi_h)
        if any(v is not None for v in roi):
            if any(v is None for v in roi):
                raise ValidationError('ROI requires all of roi_x, roi_y, roi_w and roi_h.')
            if self.roi_w <= 0 or self.roi_h <= 0:
                raise ValidationError('ROI width and height must be positive.')
            if self.roi_x + self.roi_w > 1.0 + 1e-6 or self.roi_y + self.roi_h > 1.0 + 1e-6:
                raise ValidationError('ROI must lie within the image.')


class GateDevice(models.Model):
    """A barrier gate: the cameras watching its lane and its relay controller."""

    DIRECTIONS = [
        ('in', 'Entry'),
        ('out', 'Exit'),
    ]
    EXIT_POLICIES = [
        ('registered', 'Only registered vehicles'),
        ('any', 'Every vehicle (plates are still read and logged)'),
    ]
    # A gate watches both directions, so it needs a camera for each. Fewer is
    # allowed — an installer configures them one at a time — but the admin says so.
    RECOMMENDED_CAMERAS = 2
    CONTROLLER_TYPES = [
        ('esp32', 'ESP32 relay controller'),
        ('simulator', 'Simulated barrier (no hardware)'),
    ]
    ARM_STATES = [
        ('up', 'Up'),
        ('down', 'Down'),
        ('moving', 'Moving'),
        ('stopped', 'Stopped'),
        ('unknown', 'Unknown'),
    ]

    name = models.CharField(max_length=100, unique=True)
    location = models.CharField(max_length=255, blank=True)
    cameras = models.ManyToManyField(
        Camera, through='GateCamera', related_name='gates', blank=True,
    )
    exit_policy = models.CharField(
        max_length=12, choices=EXIT_POLICIES, default='registered',
        help_text='What happens when a camera watching the exit reads a vehicle. '
                  '"Every vehicle" still reads and logs the plate, but opens regardless.',
    )
    controller_type = models.CharField(
        max_length=10, choices=CONTROLLER_TYPES, default='esp32',
        help_text='A simulated barrier receives commands even in shadow mode, since it moves no hardware.',
    )
    controller_url = models.URLField(blank=True)
    controller_token_encrypted = models.TextField(blank=True, editable=False)
    has_safety_input = models.BooleanField(
        default=False,
        help_text="A loop detector or IR beam is wired to the barrier controller's safety input.",
    )
    is_enabled = models.BooleanField(default=True)
    last_seen = models.DateTimeField(null=True, blank=True, editable=False)
    firmware_version = models.CharField(max_length=50, blank=True, editable=False)
    last_command_result = models.CharField(max_length=100, blank=True, editable=False)
    arm_state = models.CharField(max_length=10, choices=ARM_STATES, blank=True, editable=False)
    arm_state_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Gate Device"
        verbose_name_plural = "Gate Devices"
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def controller_token_set(self):
        return bool(self.controller_token_encrypted)

    def set_controller_token(self, raw):
        self.controller_token_encrypted = encrypt_secret(raw) if raw else ''

    def get_controller_token(self):
        return decrypt_secret(self.controller_token_encrypted)

    @property
    def is_simulated(self):
        return self.controller_type == 'simulator'

    @property
    def watches_both_directions(self):
        """A gate should see vehicles arriving and leaving."""
        directions = {link.direction for link in self.gate_cameras.all()}
        return {'in', 'out'} <= directions

    def camera_warning(self):
        """Why this gate's cameras are not enough yet, or '' when they are."""
        links = list(self.gate_cameras.all())
        if len(links) < self.RECOMMENDED_CAMERAS:
            return (f'{len(links)} of {self.RECOMMENDED_CAMERAS} cameras assigned. '
                    f'A gate needs one watching vehicles arriving and one watching them leave.')
        directions = {link.direction for link in links}
        if not {'in', 'out'} <= directions:
            missing = 'entry' if 'in' not in directions else 'exit'
            return f'No camera is watching the {missing} direction.'
        return ''

    def is_online(self, now=None):
        if self.is_simulated:
            return True
        if not self.last_seen:
            return False
        now = now or timezone.now()
        timeout = getattr(settings, 'GATE_HEARTBEAT_TIMEOUT_SECONDS', 30)
        return (now - self.last_seen).total_seconds() <= timeout


class GateCamera(models.Model):
    """
    A camera watching one direction of a gate. A gate normally has two: one
    looking at vehicles arriving, one at vehicles leaving.
    """

    gate = models.ForeignKey(GateDevice, on_delete=models.CASCADE, related_name='gate_cameras')
    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name='gate_links')
    direction = models.CharField(max_length=4, choices=GateDevice.DIRECTIONS, default='in')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Gate Camera"
        verbose_name_plural = "Gate Cameras"
        ordering = ['direction', 'id']
        constraints = [
            models.UniqueConstraint(fields=['gate', 'camera'], name='unique_gate_camera'),
        ]

    def __str__(self):
        return f'{self.camera} watching {self.get_direction_display().lower()} at {self.gate}'
class AccessEvent(models.Model):
    """One gate decision: automatic grant/deny or a manual override."""

    DECISIONS = [
        ('granted', 'Granted'),
        ('denied', 'Denied'),
        ('manual', 'Manual'),
    ]
    REASONS = [
        ('whitelist_hit', 'Registered vehicle'),
        ('no_plate', 'No plate detected'),
        ('no_consensus', 'Frames disagree'),
        ('low_confidence', 'Low confidence'),
        ('not_registered', 'Not registered'),
        ('expired', 'Registration expired'),
        ('not_yet_valid', 'Registration not yet valid'),
        ('inactive', 'Registration inactive'),
        ('device_disabled', 'Gate disabled'),
        ('inference_timeout', 'Recognition timed out'),
        ('processing_error', 'Recognition failed'),
        ('manual_override', 'Manual override'),
        ('exit_free', 'Exit open to every vehicle'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    gate = models.ForeignKey(
        GateDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name='events',
    )
    plate_raw = models.CharField(max_length=64, blank=True)
    plate_normalized = models.CharField(max_length=20, blank=True, db_index=True)
    confidence = models.FloatField(null=True, blank=True)
    frames_read = models.PositiveIntegerField(default=0)
    frames_agreed = models.PositiveIntegerField(default=0)
    vehicle = models.ForeignKey(
        Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name='events',
    )
    near_miss_vehicle = models.ForeignKey(
        Vehicle, null=True, blank=True, on_delete=models.SET_NULL, related_name='near_miss_events',
    )
    camera = models.ForeignKey(
        Camera, null=True, blank=True, on_delete=models.SET_NULL, related_name='events',
    )
    direction = models.CharField(
        max_length=4, choices=GateDevice.DIRECTIONS, blank=True,
        help_text='Which way the vehicle was going, from the camera that read it.',
    )
    decision = models.CharField(max_length=10, choices=DECISIONS, db_index=True)
    reason = models.CharField(max_length=24, choices=REASONS, db_index=True)
    mode = models.CharField(max_length=10, default='shadow')
    uploaded_image = models.ForeignKey(
        UploadedImage, null=True, blank=True, on_delete=models.SET_NULL, related_name='access_events',
        help_text='The frame the plate was read from.',
    )
    frames = models.ManyToManyField(
        UploadedImage, blank=True, related_name='frame_events',
        help_text='Every frame of the burst that was kept, including the one above. '
                  'Which ones survive is set by GATE_KEEP_FRAMES.',
    )
    command = models.CharField(max_length=10, blank=True)
    command_sent = models.BooleanField(default=False)
    command_result = models.CharField(max_length=100, blank=True)
    decision_latency_ms = models.PositiveIntegerField(null=True, blank=True)
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    is_test = models.BooleanField(
        default=False, db_index=True,
        help_text='Created from the admin gate test page, not by a vehicle at the gate.',
    )

    class Meta:
        verbose_name = "Access Event"
        verbose_name_plural = "Access Events"
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.plate_normalized or '-'} {self.decision}"


class SimulatedBarrier(models.Model):
    """
    State of a simulated barrier arm for a gate whose controller_type is
    'simulator'. Motion is computed from timestamps, so no background process
    is needed: the state advances whenever it is read.
    """

    PHASES = [
        ('down', 'Down'),
        ('moving_up', 'Moving up'),
        ('up', 'Up'),
        ('moving_down', 'Moving down'),
        ('stopped', 'Stopped'),
    ]

    gate = models.OneToOneField(GateDevice, on_delete=models.CASCADE, related_name='simulator')
    travel_seconds = models.FloatField(
        default=3.0, validators=[MinValueValidator(0.1), MaxValueValidator(60.0)],
        help_text='Time for the arm to travel fully up or down.',
    )
    auto_close_seconds = models.PositiveIntegerField(
        default=10,
        help_text="Close automatically this long after reaching the top, like the controller's TIMING setting. 0 disables.",
    )
    phase = models.CharField(max_length=12, choices=PHASES, default='down', editable=False)
    phase_started_at = models.DateTimeField(default=timezone.now, editable=False)
    start_position = models.FloatField(default=0.0, editable=False)
    last_nonce = models.BigIntegerField(default=0, editable=False)
    last_motion_command = models.CharField(max_length=10, blank=True, editable=False)
    last_motion_command_at = models.DateTimeField(null=True, blank=True, editable=False)
    history = models.JSONField(default=list, blank=True, editable=False)

    class Meta:
        verbose_name = "Simulated Barrier"
        verbose_name_plural = "Simulated Barriers"

    def __str__(self):
        return f"Simulated barrier for {self.gate.name}"


class GateConfigChange(models.Model):
    """Audit trail of changes to cameras and gate devices."""

    ACTIONS = [
        ('create', 'Created'),
        ('update', 'Updated'),
        ('delete', 'Deleted'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    object_type = models.CharField(max_length=20)
    object_id = models.PositiveIntegerField()
    object_repr = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=10, choices=ACTIONS)
    changes = models.JSONField(default=dict)

    class Meta:
        verbose_name = "Gate Config Change"
        verbose_name_plural = "Gate Config Changes"
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['object_type', 'object_id'])]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.object_type} {self.object_id}"
