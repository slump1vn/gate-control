"""
Forms shared by the Django admin and the gate management API, so both
validate cameras, gate devices and vehicles the same way.
"""

from django import forms

from .models import Camera, GateDevice, Vehicle
from .services import camera_service
from .utils.secrets import SecretKeyMissing, encrypt_secret


def _check_secret_key(value):
    """Reject a secret that was entered but cannot be encrypted."""
    if value:
        try:
            encrypt_secret('probe')
        except SecretKeyMissing as exc:
            raise forms.ValidationError(str(exc))
    return value


class CameraForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Leave blank to keep the current password.',
    )

    class Meta:
        model = Camera
        fields = [
            'name', 'is_enabled', 'host', 'rtsp_port', 'http_port', 'username', 'vendor',
            'main_stream_path', 'sub_stream_path', 'snapshot_path', 'live_snapshot_path', 'prefer_snapshot',
            'roi_x', 'roi_y', 'roi_w', 'roi_h', 'motion_threshold', 'settle_ms', 'cooldown_s',
        ]

    def clean_host(self):
        try:
            return camera_service.validate_host_syntax(self.cleaned_data.get('host'))
        except camera_service.CameraHostError as exc:
            raise forms.ValidationError(str(exc))

    def clean_password(self):
        return _check_secret_key(self.cleaned_data.get('password'))

    def clean(self):
        cleaned = super().clean()
        host = cleaned.get('host')
        if host:
            try:
                camera_service.resolve_allowed_host(host)
            except camera_service.CameraHostError as exc:
                self.add_error('host', str(exc))
        return cleaned


class GateDeviceForm(forms.ModelForm):
    controller_token = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Token shared with the ESP32 controller. Leave blank to keep the current token.',
    )

    class Meta:
        model = GateDevice
        fields = [
            'name', 'location', 'controller_type', 'controller_url', 'exit_policy',
            'has_safety_input', 'is_enabled',
        ]

    def clean_controller_token(self):
        return _check_secret_key(self.cleaned_data.get('controller_token'))


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            'plate_display', 'owner_name', 'owner_phone', 'department', 'vehicle_type',
            'valid_from', 'valid_until', 'is_active', 'notes',
        ]
