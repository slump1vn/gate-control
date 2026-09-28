"""
Controller contract v2: every request to a controller is signed, and the
controller token never travels. Same function as lpr_app/utils/gate_signing.py
and the firmware's guard.cpp; all three are tested against TEST_VECTOR.

    X-Gate-Sig = hex(HMAC-SHA256(token, METHOD \\n PATH \\n NONCE \\n TS \\n hex(SHA-256(body))))
"""

import hashlib
import hmac

NONCE_HEADER = 'X-Gate-Nonce'
TS_HEADER = 'X-Gate-Ts'
SIGNATURE_HEADER = 'X-Gate-Sig'

TEST_VECTOR = {
    'secret': 'test-secret-0123456789',
    'method': 'POST',
    'path': '/open',
    'nonce': '1727500000000',
    'ts': '1727500000',
    'body': b'{}',
    'signature': '559348f51d1a09266f244fb2f15e33e2e8cf3376e0685785381bedd9e078241c',
}


def sign(secret, method, path, nonce, ts, body):
    digest = hashlib.sha256(body or b'').hexdigest()
    message = f'{method.upper()}\n{path}\n{nonce}\n{ts}\n{digest}'.encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def headers(secret, method, path, nonce, ts, body):
    nonce, ts = str(nonce), str(ts)
    return {
        NONCE_HEADER: nonce,
        TS_HEADER: ts,
        SIGNATURE_HEADER: sign(secret, method, path, nonce, ts, body),
    }
