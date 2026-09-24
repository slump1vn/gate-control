"""
Audit trail for camera and gate device configuration changes.

Secret fields are recorded only as 'changed', never with their values.
"""

from ..models import Camera, GateConfigChange, GateDevice
from . import camera_service

SECRET_FIELDS = {
    Camera: 'password_encrypted',
    GateDevice: 'controller_token_encrypted',
}

AUDITED_FIELDS = {
    Camera: [
        'name', 'is_enabled', 'host', 'rtsp_port', 'http_port', 'username', 'vendor',
        'main_stream_path', 'sub_stream_path', 'snapshot_path', 'live_snapshot_path', 'prefer_snapshot',
        'roi_x', 'roi_y', 'roi_w', 'roi_h', 'motion_threshold', 'settle_ms', 'cooldown_s',
    ],
    GateDevice: [
        'name', 'location', 'controller_type', 'controller_url', 'exit_policy',
        'has_safety_input', 'is_enabled',
    ],
}

# Public names for secret fields in the audit record
SECRET_LABELS = {
    'password_encrypted': 'password',
    'controller_token_encrypted': 'controller_token',
}


def snapshot(obj):
    """Capture audited field values (and secret ciphertext) before a change."""
    if obj is None or obj.pk is None:
        return None
    model = type(obj)
    values = {f: getattr(obj, f) for f in AUDITED_FIELDS[model]}
    values[SECRET_FIELDS[model]] = getattr(obj, SECRET_FIELDS[model])
    return values


def diff(before, obj):
    model = type(obj)
    changes = {}
    for field in AUDITED_FIELDS[model]:
        old = before.get(field) if before else None
        new = getattr(obj, field)
        if before is None or old != new:
            changes[field] = {'old': old, 'new': new}
    secret = SECRET_FIELDS[model]
    old_secret = before.get(secret) if before else ''
    if (old_secret or '') != (getattr(obj, secret) or ''):
        changes[SECRET_LABELS[secret]] = 'changed'
    return changes


def record_change(user, obj, before, action=None):
    """
    Write a GateConfigChange for obj. `before` is the snapshot() taken before
    the save (None for a create). Returns the entry, or None if nothing changed.
    """
    model = type(obj)
    if action is None:
        action = 'create' if before is None else 'update'
    changes = diff(before, obj) if action != 'delete' else {}
    if action == 'update' and not changes:
        return None
    return GateConfigChange.objects.create(
        user=user if getattr(user, 'is_authenticated', False) else None,
        object_type=model.__name__.lower(),
        object_id=obj.pk,
        object_repr=str(obj)[:255],
        action=action,
        changes=_json_safe(changes),
    )


def bump_version_if_changed(camera, before):
    """Increment a camera's config_version when any audited value changed."""
    if before is not None and diff(before, camera):
        camera.config_version += 1


def _db_snapshot(obj):
    if obj.pk is None:
        return None
    current = type(obj).objects.filter(pk=obj.pk).first()
    return snapshot(current) if current else None


def save_camera(camera, user, password=None):
    """
    Save a camera from the admin or the API: set the password only if one was
    given, fill vendor preset paths, bump config_version, audit the change.
    """
    before = _db_snapshot(camera)
    if password:
        camera.set_password(password)
    camera_service.apply_preset(camera)
    bump_version_if_changed(camera, before)
    camera.updated_by = user if getattr(user, 'is_authenticated', False) else None
    camera.save()
    record_change(user, camera, before)
    return camera


def save_gate_device(gate, user, token=None):
    """
    Save a gate device, setting the controller token only if one was given.
    A simulated gate gets a generated token and its simulator state if missing.
    """
    from ..models import SimulatedBarrier
    from ..utils.secrets import generate_token

    before = _db_snapshot(gate)
    if token:
        gate.set_controller_token(token)
    elif gate.is_simulated and not gate.controller_token_set:
        gate.set_controller_token(generate_token())
    gate.save()
    if gate.is_simulated:
        SimulatedBarrier.objects.get_or_create(gate=gate)
    record_change(user, gate, before)
    return gate


def record_user_change(actor, user, action, changes):
    """Audit a user create/update/deactivate, alongside camera/gate changes."""
    return GateConfigChange.objects.create(
        user=actor if getattr(actor, 'is_authenticated', False) else None,
        object_type='user',
        object_id=user.pk,
        object_repr=str(user)[:255],
        action=action,
        changes=_json_safe(changes),
    )


def delete_with_audit(obj, user):
    from .camera_service import forget_live_session

    record_change(user, obj, None, action='delete')
    if isinstance(obj, Camera):
        forget_live_session(obj.pk)
    obj.delete()


def _json_safe(changes):
    def conv(v):
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        return str(v)
    return {k: conv(v) for k, v in changes.items()}
