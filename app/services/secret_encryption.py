"""Encryption-at-rest helpers for webhook signing secrets (issue #266).

Webhook signing secrets must never be stored as recoverable plaintext in the
database. Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
before persistence and decrypted only when the secret is needed (signing,
rotation bookkeeping).

Key resolution:

- ``WEBHOOK_SECRET_ENCRYPTION_KEY`` (a Fernet key: 32 url-safe base64-encoded
  bytes) is required in non-local environments and is the production key.
- When it is unset (local/test development), a deterministic key is derived
  from ``SECRET_KEY`` so the default configuration still encrypts at rest.

Legacy values written before this feature are stored as plaintext; reading
them returns the value unchanged so a pre-migration row never crashes a
webhook read. The Alembic migration (0029_encrypt_webhook_secrets) rewrites
those rows to ciphertext.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# Every Fernet token starts with the base64-encoded version byte (0x80).
_FERNET_PREFIX = "gAAAAA"


def _fernet() -> Fernet:
    """Build a Fernet cipher from the configured encryption key."""
    raw_key = settings.WEBHOOK_SECRET_ENCRYPTION_KEY
    if raw_key:
        key = raw_key.encode("utf-8")
    else:
        # Local/test fallback: derive a stable key from SECRET_KEY so the
        # default configuration still never stores plaintext.
        digest = hashlib.sha256((settings.SECRET_KEY or "apexchainx-dev-secret").encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def is_encrypted(value: str) -> bool:
    """Return True if *value* looks like a Fernet token (vs. legacy plaintext)."""
    return value.startswith(_FERNET_PREFIX)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret for storage, returning a Fernet token."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a stored Fernet token back to plaintext.

    Legacy plaintext values (written before encryption-at-rest) are returned
    unchanged, and unreadable tokens are returned as stored rather than
    crashing webhook reads.
    """
    if not is_encrypted(token):
        return token
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return token
