"""Credential-stuffing detection scoped to (IP, account) pairs (#507, #629).

The detector used to lock on the source IP alone, so an attacker spraying
passwords from a shared office NAT locked out every legitimate user behind
that address. Attempts are now recorded in three scopes — per (IP, account)
pair, per account across all source IPs, and IP-wide — and only the pair and
account scopes may lock a login. The IP-wide signal exists solely for
alerting: a shared address spraying many accounts is worth a signal, but the
innocent accounts behind that address must keep working.

Every scope key gets a TTL of the rolling window (plus margin) so counters
cannot accumulate forever, and account identities are hashed into the key
rather than stored as plaintext.

The persistent ledger (``app/services/auth_attempt_ledger.py``) mirrors the
per-account prefix counts in Postgres so detection survives a Redis restart;
it is best-effort — when Redis is unavailable the detector degrades to the
ledger alone, and vice versa.
"""

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

    # ------------------------------------------------------------------
    # Key construction
    # ------------------------------------------------------------------

    @staticmethod
    def _bounded_ip(ip: str) -> str:
        # IPv6 addresses and exotic headers can exceed Redis key comfort; long
        # values are hashed so the key stays bounded without losing uniqueness.
        if len(ip) <= 64:
            return ip
        return hashlib.sha256(ip.encode("utf-8")).hexdigest()

    def _pair_key(self, ip: str, account: str) -> str:
        return f"cred_stuffing:pair:{self._bounded_ip(ip)}:{self._account_hash(account)}"

    def _ip_key(self, ip: str) -> str:
        return f"cred_stuffing:ip:{self._bounded_ip(ip)}"

    def _account_key(self, account: str) -> str:
        return f"cred_stuffing:account:{self._account_hash(account)}"

    @staticmethod
    def _account_hash(account: str) -> str:
        # Accounts (email addresses) are PII: hash them into the key instead of
        # storing them in plaintext under a Redis prefix.
        return hashlib.sha256(account.strip().lower().encode("utf-8")).hexdigest()

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
        """Record one login attempt in every scope that applies.

        ``account`` scopes (pair + account-wide) are only written when the
        account is known; a bare ``ip`` attempt lands in the IP scope only, so
        legacy call-sites keep working without polluting per-account buckets.
        """
        prefix = password[:4]
        now = time()
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60

        # Persistent mirror (Postgres) for the account scope: survives Redis
        # loss. Best-effort — the detector still works without it.
        if db is not None and account:
            try:
                record_credential_prefix(
                    db,
                    account.strip().lower(),
                    password,
                    settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD + 1,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Credential-stuffing ledger write failed: %s", exc)

        try:
            if account:
                pair_key = self._pair_key(ip, account)
                self.redis.zadd(pair_key, {prefix: now})
                self.redis.zremrangebyscore(pair_key, "-inf", now - window)
                self.redis.expire(pair_key, int(window) + 60)

                account_key = self._account_key(account)
                # The member folds in the source IP: a distributed spray reuses
                # the same short passwords from every bot, so bare prefixes
                # would collapse 6 IPs × 15 guesses to 15 and the account
                # scope would never trip. (prefix, ip) pairs make the
                # account-wide zset count attempts, not distinct prefixes.
                self.redis.zadd(account_key, {f"{prefix}:{self._bounded_ip(ip)}": now})
                self.redis.zremrangebyscore(account_key, "-inf", now - window)
                self.redis.expire(account_key, int(window) + 60)

            # IP-wide scope always gets an entry so the alerting signal (and
            # only that) sees attempts regardless of account knowledge. The
            # member folds in the account identity: a shared address spraying
            # many accounts with the same short passwords is exactly the spray
            # shape worth alerting on, and bare-prefix members would collapse
            # it to nothing.
            ip_key = self._ip_key(ip)
            member = f"{prefix}:{self._account_hash(account)}" if account else prefix
            self.redis.zadd(ip_key, {member: now})
            self.redis.zremrangebyscore(ip_key, "-inf", now - window)
            self.redis.expire(ip_key, int(window) + 60)
        except Exception as exc:
            logger.warning("Credential-stuffing Redis counter unavailable: %s", exc)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_stuffing(
        self,
        ip: str,
        account: str | None = None,
        db: Session | None = None,
    ) -> bool:
        """Return True when this (IP, account) pair looks like a spray.

        Without an ``account`` the IP-wide signal is used, which is only safe
        for alerting — see the module docstring for why it must not lock.
        """
        if not account:
            return self._count(self._ip_key(ip)) >= settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD

        # Pair scope only: a "yes" here means *this* IP is spraying *this*
        # account. The distributed shape — many IPs, one account, each pair
        # under the threshold — is is_account_locked()'s job, and it must not
        # leak into the pair decision: locking every visitor behind one
        # borderline IP because the account is under attack elsewhere would
        # reintroduce the shared-NAT lockout this detector exists to prevent.
        return self._count(self._pair_key(ip, account)) >= settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD

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
        return self._count(self._account_key(account)) >= settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD

    # ------------------------------------------------------------------
    # Counts (audit payloads / dashboards)
    # ------------------------------------------------------------------

    def get_suspicious_ip_count(
        self,
        ip: str,
        db: Session | None = None,
        account: str | None = None,
    ) -> int:
        """Unique-prefix count for the audit payload.

        With an ``account`` this is the (IP, account) pair scope; without, the
        IP-wide scope. When a session is supplied, the persistent ledger's
        account-scope count is folded in (max) so the number stays meaningful
        after a Redis loss.
        """
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        counts = [self._count(self._pair_key(ip, account) if account else self._ip_key(ip))]
        if db is not None:
            try:
                counts.append(
                    get_credential_prefix_count(db, (account or ip).strip().lower(), window)
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Credential-stuffing ledger read failed: %s", exc)
        return max(counts)

    def get_suspicious_pair_count(self, ip: str, account: str) -> int:
        return self._count(self._pair_key(ip, account))

    def get_suspicious_account_count(self, account: str) -> int:
        if not account:
            return 0
        return self._count(self._account_key(account))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _count(self, key: str) -> int:
        """Unique password prefixes in the rolling window for one scope key."""
        now = time()
        window = settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60
        try:
            self.redis.zremrangebyscore(key, "-inf", now - window)
            members = self.redis.zrangebyscore(key, now - window, "+inf")
            unique = {m.decode() if isinstance(m, bytes) else m for m in members}
            return len(unique)
        except Exception as exc:
            logger.warning("Credential-stuffing Redis counter unavailable: %s", exc)
            return 0

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
