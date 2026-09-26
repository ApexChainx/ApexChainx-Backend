"""Refuses to boot with the known-default SECRET_KEY outside a
development environment, closing a forgeable-session/impersonation risk.
"""

KNOWN_DEFAULT_SECRET_KEY = "apexchainx-dev-secret"


class InsecureSecretKeyConfig(Exception):
    pass


def assert_secret_key_is_safe(app_env: str, secret_key: str) -> None:
    if app_env != "development" and secret_key == KNOWN_DEFAULT_SECRET_KEY:
        raise InsecureSecretKeyConfig(
            "SECRET_KEY must be overridden outside the development environment"
        )
