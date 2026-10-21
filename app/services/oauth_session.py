"""OAuth session state management backed by Redis.

Stores OAuth state parameters with TTL for PKCE and anti-CSRF protection.

#568: every stored row also carries an ``expires_at`` timestamp. Redis' TTL is
the primary expiry mechanism, but the timestamp makes staleness visible inside
the payload so rows can be expired lazily on read and swept by the periodic
pruning task even if they were written by a path that lost its TTL (e.g. a
RESTORE or a future non-Redis backend).
"""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime

from redis import Redis

from app.core.config import settings
from app.utils.logging import get_structured_logger

logger = get_structured_logger("oauth_session")

STATE_KEY_PREFIX = "oauth_state:"


class OAuthStateRepository:
    def __init__(self, redis_client: Redis | None = None):
        self.redis = redis_client or Redis.from_url(settings.CELERY_BROKER_URL)
        self.ttl = settings.OAUTH_STATE_TTL_SECONDS

    def _state_key(self, state: str) -> str:
        return f"{STATE_KEY_PREFIX}{state}"

    def create_state(self, provider: str, redirect_uri: str, code_challenge: str | None = None) -> str:
        state = f"oauth_state_{secrets.token_hex(16)}"
        now = datetime.now(UTC)
        payload = {
            "provider": provider,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "created_at": now.isoformat(),
            # #568: expiry is stamped in the payload so stale rows can be
            # identified (and pruned) independently of the Redis TTL.
            "expires_at": datetime.fromtimestamp(now.timestamp() + self.ttl, tz=UTC).isoformat(),
        }
        self.redis.setex(self._state_key(state), self.ttl, json.dumps(payload))
        return state

    @staticmethod
    def _is_expired(payload: dict | None, now: datetime | None = None) -> bool:
        """True when the row's expires_at has passed (or cannot be trusted)."""
        if not payload:
            return False
        raw = payload.get("expires_at")
        if not raw:
            # Legacy row written before #568: rely on the Redis TTL alone.
            return False
        try:
            expires_at = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            logger.warning("OAuth state row has an unparseable expires_at; treating as expired")
            return True
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return (now or datetime.now(UTC)) >= expires_at

    def _load(self, state: str) -> dict | None:
        """Read and parse a state row; deletes and returns None when unusable."""
        key = self._state_key(state)
        data = self.redis.get(key)
        if data is None:
            return None
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            logger.warning("OAuth state row is not valid JSON; deleting it")
            self.redis.delete(key)
            return None
        if self._is_expired(payload):
            # #568: lazy expiry on read — a stale row is removed even if the
            # TTL has not fired yet.
            self.redis.delete(key)
            return None
        return payload

    def consume_state(self, state: str) -> dict | None:
        key = self._state_key(state)
        data = self.redis.get(key)
        if data is None:
            return None
        self.redis.delete(key)
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            logger.warning("OAuth state row is not valid JSON; treating as consumed")
            return None
        if self._is_expired(payload):
            return None
        return payload

    def get_state(self, state: str) -> dict | None:
        return self._load(state)

    def prune_expired_states(self, batch_size: int | None = None) -> int:
        """Delete expired/corrupt oauth state rows; returns how many (#568).

        Called periodically by app.tasks.oauth_tasks so rows that lost their
        TTL (or whose TTL a future backend ignores) cannot accumulate forever.
        """
        count = batch_size or settings.OAUTH_STATE_PRUNE_BATCH_SIZE
        deleted = 0
        for key in self.redis.scan_iter(match=f"{STATE_KEY_PREFIX}*", count=count):
            data = self.redis.get(key)
            if data is None:
                # Expired between the scan and the read — nothing to do.
                continue
            try:
                payload = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                payload = None
            if payload is None or self._is_expired(payload):
                self.redis.delete(key)
                deleted += 1
        return deleted

    @staticmethod
    def verify_code_challenge(code_verifier: str, code_challenge: str) -> bool:
        expected = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
        return hmac.compare_digest(expected_b64, code_challenge)


# Singleton
oauth_state_repo = OAuthStateRepository()
