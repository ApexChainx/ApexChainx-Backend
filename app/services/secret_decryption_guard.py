"""Raises on a Fernet-prefixed value that fails to decrypt, instead of
silently returning the ciphertext as if it were the plaintext secret.
"""

FERNET_PREFIX = b"gAAAAA"


class SecretDecryptionFailed(Exception):
    pass


def guard_decrypt(stored_value: bytes, decrypt_fn, invalid_token_exc) -> bytes:
    is_fernet_value = stored_value.startswith(FERNET_PREFIX)
    try:
        return decrypt_fn(stored_value)
    except invalid_token_exc as exc:
        if is_fernet_value:
            raise SecretDecryptionFailed("Fernet-prefixed secret failed to decrypt") from exc
        return stored_value
