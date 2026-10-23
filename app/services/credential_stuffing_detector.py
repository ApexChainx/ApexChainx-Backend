"""Credential-stuffing detection scoped to (IP, account) pairs (#507).

The detector used to lock on the source IP alone, so an attacker spraying from
a shared office NAT locked out every legitimate user behind that address. The
signal is now bucketed on three scopes:

- ``cred_stuffing:ip:{ip}``          — IP-wide spray signal (alerting only)
- ``cred_stuffing:pair:{ip}:{acct}`` — one attacker address against one account
- ``cred_stuffing:account:{acct}``   — a distributed spray on a single account,
  which no per-IP heuristic can see

Account identity is hashed into the key and normalised (case/whitespace) so
trivial variations do not create a second bucket. Every written key carries a
TTL so the window cannot grow without bound. Redis unavailability degrades to
a logged warning and an open signal rather than blocking logins.
"""

from __future__ import annotations

import hashlib
import logging
from time import time

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.auth_attempt_ledger import (
    get_credential_prefix_count,
    record_credential_prefix,
)

logger = logging.getLogger(__name__)


class CredentialStuffingDetector:
    def __init__(self, redis_client: Redis | None = None) -> None:
        self.redis = redis_client or Redis.from_url(settings.CELERY_BROKER_URL)

    # ------------------------------------------------------------------
    # Key construction
    # ------------------------------------------------------------------

    @staticmethod
    def _bounded_ip(ip: str) -> str:
        """Keep raw addresses out of long-lived keys when oversized."""
        return ip if len(ip) <= 64 else hashlib.sha256(ip.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_account(account: str | None) -> str:
        """Case/whitespace variants of one email must share a bucket."""
        return (account or "").strip().lower()

    @classmethod
    def _account_hash(cls, account: str) -> str:
        return hashlib.sha256(cls._normalize_account(account).encode("utf-8")).hexdigest()

    def _ip_key(self, ip: str) -> str:
        return f"cred_stuffing:ip:{self._bounded_ip(ip)}"

    def _pair_key(self, ip: str, account: str) -> str:
        return f"cred_stuffing:pair:{self._bounded_ip(ip)}:{self._account_hash(account)}"

    def _account_key(self, account: str) -> str:
        return f"cred_stuffing:account:{self._account_hash(account)}"

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_attempt(
        self,
        ip: str,
        password: str,
        account: str | None = None,
        db: Session | None = None,
    ) -> None:
        # Persistent ledger (DB) mirrors the same scope: the account when one
        # is given, otherwise the IP, so the signal survives a Redis outage.
        if db is not None:
            record_credential_prefix(
                db,
                self._normalize_account(account) or ip,
                password,
                settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD + 1,
            )

        prefix = password[:4]
        now = time()
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        # Each scope's member must be unique per the dimension that scope
        # counts, otherwise a spray that reuses prefixes across accounts (or
        # source IPs) collapses into a single entry and is never detected:
        # - ip scope:      distinct (account, prefix) pairs seen from this IP
        # - pair scope:    distinct prefixes for this (IP, account) pair
        # - account scope: distinct (IP, prefix) pairs against this account
        ip_member = f"{self._account_hash(account)}:{prefix}" if account else prefix
        try:
            entries: list[tuple[str, str]] = [(self._ip_key(ip), ip_member)]
            if account:
                entries.append((self._pair_key(ip, account), prefix))
                entries.append((self._account_key(account), f"{self._bounded_ip(ip)}:{prefix}"))
            for key, member in entries:
                self.redis.zadd(key, {member: now})
                self.redis.zremrangebyscore(key, "-inf", now - window)
                self.redis.expire(key, int(window) + 60)
        except (RedisError, OSError) as exc:
            # Fail-open by policy: an unavailable counter must never block a
            # legitimate login, so Redis outages only degrade the signal.
            logger.warning("Credential-stuffing Redis counter unavailable: %s", exc)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _count(self, key: str) -> int:
        """Distinct prefixes seen for *key* inside the window."""
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        now = time()
        try:
            self.redis.zremrangebyscore(key, "-inf", now - window)
            unique = self.redis.zrangebyscore(key, now - window, "+inf")
            return len({u.decode() if isinstance(u, bytes) else u for u in unique})
        except (RedisError, OSError) as exc:
            logger.warning("Credential-stuffing Redis counter unavailable: %s", exc)
            return 0

    def detect_stuffing(self, ip: str, account: str | None = None) -> bool:
        """Return True when this (IP, account) pair looks like a spray.

        Without an ``account`` the IP-wide signal is used, which is only safe
        for alerting — see the module docstring for why it must not lock.
        """
        key = self._pair_key(ip, account) if account else self._ip_key(ip)
        return self._count(key) >= settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD

    def is_ip_flagged(self, ip: str) -> bool:
        """IP-wide spray signal. Alerting only — never blocks a request."""
        return self._count(self._ip_key(ip)) >= settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD

    def is_account_locked(self, account: str) -> bool:
        """Account-wide spray signal across every source IP.

        This is the scope that catches a distributed attack on one account; the
        per-pair scope cannot see it because each IP stays under the threshold.
        """
        if not account:
            return False
        return self._count(self._account_key(account)) >= settings.AUTH_ACCOUNT_STUFFING_ENTROPY_THRESHOLD

    # ------------------------------------------------------------------
    # Counts (audit payloads / dashboards)
    # ------------------------------------------------------------------

    def get_suspicious_ip_count(
        self,
        ip: str,
        db: Session | None = None,
        account: str | None = None,
    ) -> int:
        """Distinct prefixes for the IP, including the persistent DB ledger.

        The persistent count is keyed on the account when one is supplied (the
        same scope ``record_attempt`` writes), so an attack that rotated IPs
        still shows up in audit payloads.
        """
        persistent_count = 0
        if db is not None:
            window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
            persistent_count = get_credential_prefix_count(
                db,
                self._normalize_account(account) or ip,
                window,
            )
        return max(persistent_count, self._count(self._ip_key(ip)))

    def get_suspicious_pair_count(self, ip: str, account: str) -> int:
        return self._count(self._pair_key(ip, account))

    def get_suspicious_account_count(self, account: str) -> int:
        if not account:
            return 0
        return self._count(self._account_key(account))

    # ------------------------------------------------------------------
    # Lockout policy
    # ------------------------------------------------------------------

    @staticmethod
    def lockout_minutes() -> int:
        """Return the lockout length, capped so it cannot grow without bound.

        The multiplier used to be a hard-coded 4x, which meant a longer
        ``AUTH_LOCKOUT_DURATION_MINUTES`` silently produced hour-long outages
        for a whole NAT.
        """
        uncapped = settings.AUTH_LOCKOUT_DURATION_MINUTES * settings.AUTH_STUFFING_LOCKOUT_MULTIPLIER
        return max(1, min(uncapped, settings.AUTH_STUFFING_LOCKOUT_MAX_MINUTES))


# Shared instance: the detector's Redis client is connection-pooled, so one
# per-process instance serves every call-site that imports it.
credential_stuffing_detector = CredentialStuffingDetector()
