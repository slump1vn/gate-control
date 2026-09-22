"""
Session login for the SPA.

The CSRF token is returned in the response body so the SPA can send it as
X-CSRFToken even when it runs on a different origin than the API.
"""

import json
import logging

from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from ..middleware.rate_limit import _get_client_ip
from ..utils.auth import user_roles

logger = logging.getLogger(__name__)

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300


def _session_payload(request):
    user = request.user
    authenticated = user.is_authenticated
    return {
        'authenticated': authenticated,
        'username': user.get_username() if authenticated else None,
        'roles': user_roles(user),
        'csrf_token': get_token(request),
    }


def _lockout_key(request, username):
    return f'login-fail:{_get_client_ip(request)}:{username.lower()}'


@require_http_methods(["GET"])
@ensure_csrf_cookie
def api_auth_me(request):
    return JsonResponse(_session_payload(request))


@require_http_methods(["POST"])
def api_auth_login(request):
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body or b'{}')
        except (ValueError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
    else:
        data = request.POST
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', ''))

    key = _lockout_key(request, username)
    failures = cache.get(key, 0)
    if failures >= MAX_FAILED_LOGINS:
        return JsonResponse({
            'success': False,
            'error': 'Too many failed attempts. Try again in a few minutes.',
            'error_code': 'LOGIN_LOCKED',
        }, status=429)

    user = authenticate(request, username=username, password=password) if username else None
    if user is None:
        cache.set(key, failures + 1, LOCKOUT_SECONDS)
        logger.warning('Failed login for "%s" from %s', username, _get_client_ip(request))
        return JsonResponse({
            'success': False,
            'error': 'Invalid username or password',
            'error_code': 'INVALID_CREDENTIALS',
        }, status=401)

    cache.delete(key)
    login(request, user)
    logger.info('User "%s" logged in from %s', username, _get_client_ip(request))
    return JsonResponse(_session_payload(request))


@require_http_methods(["POST"])
def api_auth_logout(request):
    logout(request)
    return JsonResponse(_session_payload(request))
