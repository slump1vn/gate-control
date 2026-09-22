"""
Encryption for secrets stored in the database (camera passwords, controller
tokens), keyed by GATE_CONFIG_ENCRYPTION_KEY.
"""

import secrets

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretKeyMissing(Exception):
    """GATE_CONFIG_ENCRYPTION_KEY is unset or malformed."""


class SecretDecryptError(Exception):
    """A stored secret cannot be decrypted with the configured key."""


def _fernet():
    key = getattr(settings, 'GATE_CONFIG_ENCRYPTION_KEY', '')
    if not key:
        raise SecretKeyMissing(
            'GATE_CONFIG_ENCRYPTION_KEY is not configured; '
            'generate one with: python manage.py generate_gate_secrets'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError):
        raise SecretKeyMissing(
            'GATE_CONFIG_ENCRYPTION_KEY is not a valid Fernet key; '
            'generate one with: python manage.py generate_gate_secrets'
        )


def encrypt_secret(plaintext):
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext):
    if not ciphertext:
        return ''
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise SecretDecryptError('Stored secret cannot be decrypted with the configured key')


def generate_encryption_key():
    return Fernet.generate_key().decode()


def generate_token():
    return secrets.token_urlsafe(32)


def tokens_equal(a, b):
    if not a or not b:
        return False
    return secrets.compare_digest(str(a), str(b))
