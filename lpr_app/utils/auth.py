"""
Access control for gate endpoints.

People authenticate with a Django session and are authorised by group:
gate_admin (everything) or gate_operator (registry, events, overrides).
The gate agent and the ESP32 controllers authenticate with bearer tokens
that only open their own endpoints.
"""

import functools
import logging
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import gate_signing
from .secrets import SecretDecryptError, SecretKeyMissing, tokens_equal

logger = logging.getLogger(__name__)

GATE_ADMIN = 'gate_admin'
GATE_OPERATOR = 'gate_operator'


def user_roles(user):
    """Roles held by a user. gate_admin implies the gate_operator scope."""
    if not user or not user.is_authenticated or not user.is_active:
        return []
    if user.is_superuser:
        return [GATE_ADMIN, GATE_OPERATOR]
    groups = set(user.groups.values_list('name', flat=True))
    if GATE_ADMIN in groups:
        return [GATE_ADMIN, GATE_OPERATOR]
    if GATE_OPERATOR in groups:
        return [GATE_OPERATOR]
    return []


def primary_role(user):
    """The single role a user is managed under: gate_admin, gate_operator, or None."""
    roles = user_roles(user)
    if GATE_ADMIN in roles:
        return GATE_ADMIN
    if GATE_OPERATOR in roles:
        return GATE_OPERATOR
    return None


def forbidden(message='Permission denied'):
    return JsonResponse(
        {'success': False, 'error': message, 'error_code': 'FORBIDDEN'},
        status=403,
    )


def require_login_unless_public(view):
    """
    Open to anyone while PUBLIC_UPLOAD_ENABLED is on; otherwise a login is
    needed. Guards the manual upload tool and the images it produces, which
    are separate from the gate's own frames.
    """
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if getattr(settings, 'PUBLIC_UPLOAD_ENABLED', False):
            return view(request, *args, **kwargs)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            return view(request, *args, **kwargs)
        return forbidden('Sign in to use this')
    return wrapped


def require_role(role):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            if role not in user_roles(request.user):
                return forbidden()
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


require_gate_admin = require_role(GATE_ADMIN)
require_gate_operator = require_role(GATE_OPERATOR)


def bearer_token(request):
    header = request.META.get('HTTP_AUTHORIZATION', '')
    if header.startswith('Bearer '):
        return header[len('Bearer '):].strip()
    return ''


def require_agent_token(view):
    """Allow only the gate agent (GATE_AGENT_TOKEN). CSRF-exempt: no cookies involved."""
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        expected = getattr(settings, 'GATE_AGENT_TOKEN', '')
        if not expected:
            logger.error('GATE_AGENT_TOKEN is not configured; rejecting agent request')
            return forbidden('Agent access is not configured')
        if not tokens_equal(bearer_token(request), expected):
            return forbidden()
        return view(request, *args, **kwargs)
    return csrf_exempt(wrapped)


def _controller_token(gate):
    try:
        return gate.get_controller_token()
    except (SecretKeyMissing, SecretDecryptError):
        logger.error('Cannot decrypt controller token for gate %s', gate.pk)
        return ''


def device_token_matches(request, gate):
    """True if the request carries the gate's controller token (contract v1)."""
    expected = _controller_token(gate)
    return bool(expected) and tokens_equal(bearer_token(request), expected)


@dataclass
class DeviceAuth:
    """How a controller-side request proved itself. nonce and ts are None for v1."""
    version: int
    nonce: Optional[int] = None
    ts: Optional[float] = None


def device_request(request, gate):
    """
    Authenticate a request signed with the gate's controller token (contract
    v2, see utils/gate_signing), or carrying it as a bearer (v1, accepted for
    one release while agents and devices move to v2). Returns DeviceAuth, or
    None when neither proves it.

    The signature covers the nonce and timestamp; checking them against what
    was seen before, and against the clock, is the caller's business.
    """
    signature = request.headers.get(gate_signing.SIGNATURE_HEADER)
    if signature is None:
        return DeviceAuth(version=1) if device_token_matches(request, gate) else None
    nonce = request.headers.get(gate_signing.NONCE_HEADER, '')
    ts = request.headers.get(gate_signing.TS_HEADER, '')
    try:
        parsed = int(nonce), float(ts)
    except ValueError:
        return None
    if not gate_signing.verify(_controller_token(gate), request.method, request.path, nonce, ts,
                               request.body, signature):
        return None
    return DeviceAuth(version=2, nonce=parsed[0], ts=parsed[1])
