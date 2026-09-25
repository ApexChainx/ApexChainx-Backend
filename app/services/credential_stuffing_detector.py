"""Credential-stuffing detection with blast-radius-limited lockouts (#507).

Attempts are bucketed by the SHA-256 hash of the first four characters of the
supplied password, so a spray of one account's leaked password list shows up as
many distinct buckets inside the rolling window.

Lockout scope used to be the source IP alone.  On a shared NAT (an office, a
carrier-grade NAT, a CI runner pool) one attacker spraying from that address
locked out every legitimate user behind it for ``AUTH_LOCKOUT_DURATION_MINUTES
* 4``.  Detection is now recorded at three scopes:

* ``cred_stuffing:ip``      - every attempt from the address (alerting only),
* ``cred_stuffing:pair``    - one (IP, account) pair, **this is what locks**,
* ``cred_stuffing:account`` - one account across every source IP, which is what
  a distributed spray looks like and what an IP-scoped detector can never see.

Account identifiers are hashed into the Redis key so no email address ends up
in a key name.
"""

from __future__ import annotations

import hashlib
from time import time

from redis import Redis

from app.core.config import settings

PREFIX_LENGTH = 4


class CredentialStuffingDetector:
    def __init__(self, redis_client: Redis | None = None) -> None:
        self.redis = redis_client or Redis.from_url(settings.CELERY_BROKER_URL)

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    @staticmethod
    def _account_hash(account: str) -> str:
        return hashlib.sha256(account.strip().lower().encode()).hexdigest()[:16]

    def _ip_key(self, ip: str) -> str:
        return f"cred_stuffing:ip:{ip}"

    def _pair_key(self, ip: str, account: str) -> str:
        return f"cred_stuffing:pair:{ip}:{self._account_hash(account)}"

    def _account_key(self, account: str) -> str:
        return f"cred_stuffing:account:{self._account_hash(account)}"

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_attempt(self, ip: str, password: str, account: str | None = None) -> None:
        """Record one failed-password attempt at every applicable scope.

        ``account`` is the login identifier from the request.  When it is absent
        only the IP scope is updated, so callers that cannot resolve an account
        keep the old alerting behaviour without gaining a lockout.
        """
        bucket = hashlib.sha256(password[:PREFIX_LENGTH].encode()).hexdigest()[:16]
        now = time()
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        ttl = int(window) + 60

        keys = [self._ip_key(ip)]
        if account:
            keys.append(self._pair_key(ip, account))
            keys.append(self._account_key(account))

        for key in keys:
            self.redis.zadd(key, {bucket: now})
            self.redis.zremrangebyscore(key, "-inf", now - window)
            self.redis.expire(key, ttl)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _count(self, key: str) -> int:
        now = time()
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        self.redis.zremrangebyscore(key, "-inf", now - window)
        unique = self.redis.zrangebyscore(key, now - window, "+inf")
        return len(set(u.decode() if isinstance(u, bytes) else u for u in unique))

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

    def get_suspicious_ip_count(self, ip: str) -> int:
        return self._count(self._ip_key(ip))

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


credential_stuffing_detector = CredentialStuffingDetector()
