"""
Access control for gate endpoints.

People authenticate with a Django session and are authorised by group:
gate_admin (everything) or gate_operator (registry, events, overrides).
The gate agent and the ESP32 controllers authenticate with bearer tokens
that only open their own endpoints.
"""

import functools
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

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


def device_token_matches(request, gate):
    """True if the request carries the gate's controller token."""
    try:
        expected = gate.get_controller_token()
    except (SecretKeyMissing, SecretDecryptError):
        logger.error('Cannot decrypt controller token for gate %s', gate.pk)
        return False
    return tokens_equal(bearer_token(request), expected)
