"""
Which model server answers: the primary (QWEN_BASE_URL), or a fallback
(QWEN_FALLBACK_BASE_URL, e.g. a llama.cpp server) when the primary fails.

Only failures of the server itself move a request to the fallback: a
timeout, a lost connection, or a 5xx. A 4xx means the request is wrong and
would be just as wrong elsewhere.

After the primary fails, requests go straight to the fallback for
QWEN_PRIMARY_RETRY_SECONDS before the primary is tried again. Without that,
every frame would first wait out the primary's timeout, and a gate decision,
which has its own deadline, would still time out.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import openai
from django.conf import settings
from openai import DefaultHttpxClient, OpenAI

from .. import metrics

logger = logging.getLogger(__name__)

PRIMARY = 'primary'
FALLBACK = 'fallback'

_lock = threading.Lock()
_primary_down_until = 0.0
_clients = {}


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    model: str
    api_key: str
    timeout: float


def endpoints():
    """The configured endpoints, primary first."""
    found = [Endpoint(PRIMARY, settings.QWEN_BASE_URL, settings.QWEN_MODEL, settings.QWEN_API_KEY,
                      settings.QWEN_REQUEST_TIMEOUT)]
    if settings.QWEN_FALLBACK_BASE_URL:
        found.append(Endpoint(
            FALLBACK,
            settings.QWEN_FALLBACK_BASE_URL,
            settings.QWEN_FALLBACK_MODEL or settings.QWEN_MODEL,
            # llama.cpp accepts any key, but the SDK refuses an empty one
            settings.QWEN_FALLBACK_API_KEY or settings.QWEN_API_KEY or 'none',
            settings.QWEN_FALLBACK_REQUEST_TIMEOUT or settings.QWEN_REQUEST_TIMEOUT,
        ))
    return found


def client_for(endpoint):
    """One SDK client per endpoint, shared, so connections are reused."""
    with _lock:
        client = _clients.get(endpoint)
        if client is None:
            client = OpenAI(
                api_key=endpoint.api_key,
                base_url=endpoint.base_url,
                http_client=DefaultHttpxClient(),
                timeout=endpoint.timeout,
                max_retries=settings.QWEN_MAX_RETRIES,
            )
            _clients[endpoint] = client
        return client


def is_server_failure(exc):
    """A failure of the model server (worth trying elsewhere), not of the request."""
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


def primary_available(now=None):
    return (now if now is not None else time.monotonic()) >= _primary_down_until


def _order(available):
    primary, *rest = available
    if not rest:
        # No fallback: always the primary, however it has been doing
        return [primary]
    return [primary] + rest if primary_available() else rest


def _primary_failed(exc):
    global _primary_down_until
    with _lock:
        was_up = primary_available()
        _primary_down_until = time.monotonic() + settings.QWEN_PRIMARY_RETRY_SECONDS
    metrics.MODEL_PRIMARY_AVAILABLE.set(0)
    if was_up:
        logger.warning('Model server %s failed (%s); using the fallback for %ss',
                       settings.QWEN_BASE_URL, type(exc).__name__, settings.QWEN_PRIMARY_RETRY_SECONDS)


def _primary_ok():
    global _primary_down_until
    with _lock:
        recovered = _primary_down_until != 0.0
        _primary_down_until = 0.0
    metrics.MODEL_PRIMARY_AVAILABLE.set(1)
    if recovered:
        logger.info('Model server %s answers again', settings.QWEN_BASE_URL)


def complete(**request):
    """
    chat.completions.create(**request) on the first endpoint that answers.
    Returns (response, endpoint). Raises the last error when none does.
    """
    available = endpoints()
    order = _order(available)
    last_error: Optional[Exception] = None
    for endpoint in order:
        try:
            response = client_for(endpoint).chat.completions.create(model=endpoint.model, **request)
        except Exception as exc:
            metrics.MODEL_REQUESTS.labels(endpoint=endpoint.name, result='error').inc()
            last_error = exc
            if not is_server_failure(exc):
                raise
            if endpoint.name == PRIMARY and len(available) > 1:
                _primary_failed(exc)
            continue
        metrics.MODEL_REQUESTS.labels(endpoint=endpoint.name, result='ok').inc()
        if endpoint.name == PRIMARY:
            _primary_ok()
        return response, endpoint
    raise last_error


def reset():
    """Forget the primary's state and the cached clients (settings changed, tests)."""
    global _primary_down_until
    with _lock:
        _primary_down_until = 0.0
        _clients.clear()
