"""Enforces the dedicated IMPERSONATION_SIGNING_KEY for impersonation
JWTs instead of silently falling back to the general SECRET_KEY.
"""


class ImpersonationKeyNotConfigured(Exception):
    pass


def get_impersonation_signing_key(impersonation_signing_key: str) -> str:
    if not impersonation_signing_key:
        raise ImpersonationKeyNotConfigured(
            "IMPERSONATION_SIGNING_KEY must be set to sign impersonation tokens"
        )
    return impersonation_signing_key
