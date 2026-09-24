"""Fail-closed token revocation check: a Redis outage now rejects the
request instead of silently treating it as 'not revoked'.
"""
from typing import Callable


class RevocationCheckUnavailable(Exception):
    pass


def is_revoked_fail_closed(token_id: str, redis_check: Callable[[str], bool]) -> bool:
    try:
        return redis_check(token_id)
    except Exception as exc:
        raise RevocationCheckUnavailable("Revocation store unavailable") from exc
