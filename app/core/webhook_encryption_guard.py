"""Requires an explicit WEBHOOK_SECRET_ENCRYPTION_KEY outside dev, so
production doesn't silently derive its Fernet key from the known
default SECRET_KEY.
"""


class InsecureEncryptionKeyConfig(Exception):
    pass


def assert_webhook_encryption_key_configured(app_env: str, webhook_encryption_key) -> None:
    if app_env != "development" and not webhook_encryption_key:
        raise InsecureEncryptionKeyConfig(
            "WEBHOOK_SECRET_ENCRYPTION_KEY must be set outside development"
        )
