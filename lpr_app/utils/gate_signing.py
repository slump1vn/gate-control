"""
Controller contract v2: signed requests between the gate agent, controllers
and this service.

A v1 request carried the controller token as a bearer, so anyone who could
see one request (a WiFi controller makes that easy) could forge the next.
A v2 request carries a signature instead, and the token never travels:

    X-Gate-Nonce: <integer, strictly increasing per sender>
    X-Gate-Ts:    <unix seconds>
    X-Gate-Sig:   hex(HMAC-SHA256(token, METHOD \\n PATH \\n NONCE \\n TS \\n hex(SHA-256(body))))

NONCE and TS are signed exactly as sent in their headers. The gate agent
(gate_agent/signing.py) and the ESP32 firmware (gate-controller/src/guard.cpp)
implement the same function and are tested against TEST_VECTOR.
"""

import hashlib
import hmac

NONCE_HEADER = 'X-Gate-Nonce'
TS_HEADER = 'X-Gate-Ts'
SIGNATURE_HEADER = 'X-Gate-Sig'

# Shared by every implementation's tests: change none of it without changing all
TEST_VECTOR = {
    'secret': 'test-secret-0123456789',
    'method': 'POST',
    'path': '/open',
    'nonce': '1727500000000',
    'ts': '1727500000',
    'body': b'{}',
    'signature': '559348f51d1a09266f244fb2f15e33e2e8cf3376e0685785381bedd9e078241c',
}


def canonical(method, path, nonce, ts, body):
    digest = hashlib.sha256(body or b'').hexdigest()
    return f'{method.upper()}\n{path}\n{nonce}\n{ts}\n{digest}'.encode()


def sign(secret, method, path, nonce, ts, body):
    return hmac.new(secret.encode(), canonical(method, path, nonce, ts, body), hashlib.sha256).hexdigest()


def verify(secret, method, path, nonce, ts, body, signature):
    if not (secret and signature):
        return False
    expected = sign(secret, method, path, nonce, ts, body)
    return hmac.compare_digest(expected, signature.strip().lower())
