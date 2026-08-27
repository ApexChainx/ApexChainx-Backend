"""Duplicate-memo detection for on-chain SLA settlement memos (#354).

The Stellar memo is the only on-chain link back to an SLA settlement result.
Its content-hash component is only 8 hex chars (32 bits), so a birthday
collision is realistic well before the volumes this system will see
(~65k memos, per #354). This module adds an explicit SETNX-based
uniqueness claim so a collision — or a recompute of an identical result —
is detected and rejected before settlement, rather than silently sharing
a reconciliation key with an unrelated payment.

Fallback: if Redis is unavailable, we do NOT block settlement outright —
a Redis outage should not halt SLA payouts. We log an audit event and let
the memo through unchecked. This mirrors app.core.rate_limiter's existing
fallback-on-Redis-unavailable pattern.
"""

from __future__ import annotations

import logging

import redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

MEMO_DEDUPE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days of dedupe coverage


class DuplicateMemoError(Exception):
    """Raised when a memo was already claimed by a prior settlement."""


class MemoDedupeChecker:
    """Thin wrapper so tests can swap in a fake client, same pattern as
    RedisRateLimiter.client in app.core.rate_limiter."""

    def __init__(self) -> None:
        self.client: redis.Redis | None = None

    def _get_client(self) -> redis.Redis:
        if self.client is None:
            self.client = redis.Redis.from_url(settings.CELERY_BROKER_URL)
        return self.client

    def claim_or_raise(self, memo: str) -> None:
        if not settings.USE_REDIS_MEMO_DEDUPE:
            return

        key = f"tx_memo:used:{memo}"
        try:
            claimed = self._get_client().set(key, "1", nx=True, ex=MEMO_DEDUPE_TTL_SECONDS)
        except RedisError:
            from app.services.audit_log import audit_log

            logger.warning("Memo uniqueness check unavailable (Redis down); allowing memo through: %s", memo)
            audit_log.log("memo_uniqueness_check_unavailable", {"memo": memo})
            return

        if not claimed:
            from app.services.audit_log import audit_log

            audit_log.log("memo_duplicate_detected", {"memo": memo})
            raise DuplicateMemoError(f"Memo '{memo}' was already used for a prior settlement")


memo_dedupe = MemoDedupeChecker()