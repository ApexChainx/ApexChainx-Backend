import hashlib
import logging
from time import time

from redis import Redis
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

    def _prefix_key(self, ip: str) -> str:
        bounded_ip = (
            ip
            if len(ip) <= 64
            else hashlib.sha256(ip.encode("utf-8")).hexdigest()
        )
        return f"cred_stuffing:{bounded_ip}"

    def record_attempt(
        self,
        ip: str,
        password: str,
        db: Session | None = None,
        account: str | None = None,
    ) -> None:
        if db is not None:
            record_credential_prefix(
                db,
                (account or ip).strip().lower(),
                password,
                settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD + 1,
            )

        prefix = password[:4]
        now = time()
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        try:
            self.redis.zadd(key, {prefix: now})
            self.redis.zremrangebyscore(key, "-inf", now - window)
            self.redis.expire(key, int(window) + 60)
        except Exception as exc:
            logger.warning("Credential-stuffing Redis counter unavailable: %s", exc)

    def detect_stuffing(
        self,
        ip: str,
        db: Session | None = None,
        account: str | None = None,
    ) -> bool:
        count = self.get_suspicious_ip_count(ip, db, account)
        return count > settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD

    def get_suspicious_ip_count(
        self,
        ip: str,
        db: Session | None = None,
        account: str | None = None,
    ) -> int:
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        persistent_count = 0
        if db is not None:
            persistent_count = get_credential_prefix_count(
                db,
                (account or ip).strip().lower(),
                window,
            )

        now = time()
        key = self._prefix_key(ip)
        try:
            self.redis.zremrangebyscore(key, "-inf", now - window)
            unique = self.redis.zrangebyscore(key, now - window, "+inf")
            redis_count = len(set(u.decode() if isinstance(u, bytes) else u for u in unique))
            return max(persistent_count, redis_count)
        except Exception as exc:
            logger.warning("Credential-stuffing Redis counter unavailable: %s", exc)
            return persistent_count

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
