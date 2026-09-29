"""Redis-outage fail-open behaviour for the request-path Redis consumers.

CI (pr.yml) runs the unit suite with a Postgres service but no Redis, which is
how this class of bug kept landing: every Redis consumer on the request path
must degrade (log + fail open, or fall back in-process) instead of turning the
request into a 500. The rate limiter (#238), idempotency middleware (#314) and
single-use token store (#267) already had that pinned; these tests pin the
same policy for:

* ``app.services.token_revocation`` — consulted by ``get_current_user`` on
  every authenticated request, so an outage used to 500 the whole API;
* ``app.services.credential_stuffing_detector`` — consulted by ``/auth/login``,
  so an outage used to make logging in impossible.

An outage is simulated with a client whose every operation raises
``RedisError``; no services or network are required.
"""

import pytest
from redis.exceptions import RedisError

from app.services import credential_stuffing_detector as csd_module
from app.services import token_revocation as tr
from app.services.credential_stuffing_detector import CredentialStuffingDetector


class ExplodingRedis:
    """Redis stand-in whose every operation raises, i.e. a hard outage."""

    def __getattr__(self, name):
        def _raise(*args, **kwargs):
            raise RedisError("connection refused")

        return _raise


class InMemoryZSetRedis:
    """Enough of the Redis sorted-set API for the stuffing detector."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.expiries: dict[str, int] = {}

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.zsets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key: str, min_score, max_score) -> None:
        members = self.zsets.get(key)
        if not members:
            return
        low = float("-inf") if min_score == "-inf" else float(min_score)
        high = float("+inf") if max_score == "+inf" else float(max_score)
        self.zsets[key] = {m: s for m, s in members.items() if not (low <= s <= high)}

    def zrangebyscore(self, key: str, min_score, max_score):
        members = self.zsets.get(key, {})
        low = float("-inf") if min_score == "-inf" else float(min_score)
        high = float("+inf") if max_score == "+inf" else float(max_score)
        return [m.encode() for m, s in members.items() if low <= s <= high]

    def expire(self, key: str, ttl: int) -> None:
        self.expiries[key] = ttl


@pytest.fixture(autouse=True)
def _clean_revocation_state(monkeypatch):
    """Isolate token_revocation's module-level breaker/fallback state per test."""
    monkeypatch.setattr(tr, "_local_revocations", {})
    monkeypatch.setattr(tr, "_unavailable_until", 0.0)
    monkeypatch.setattr(tr, "_revocation_redis", None)
    yield


class TestTokenRevocationOutage:
    def test_revoke_does_not_raise_when_redis_is_down(self, monkeypatch):
        monkeypatch.setattr(tr, "_revocation_redis", ExplodingRedis())

        tr.revoke("t" * 64, 60)  # must not raise

    def test_locally_revoked_token_is_detected_during_outage(self, monkeypatch):
        """POST /auth/revoke keeps its contract within the process."""
        monkeypatch.setattr(tr, "_revocation_redis", ExplodingRedis())

        tr.revoke("t" * 64, 60)

        assert tr.is_revoked("t" * 64) is True

    def test_unknown_token_fails_open_during_outage(self, monkeypatch):
        """An outage must not 500 the next authenticated request."""
        monkeypatch.setattr(tr, "_revocation_redis", ExplodingRedis())

        assert tr.is_revoked("u" * 64) is False

    def test_open_circuit_fails_open_without_touching_redis(self, monkeypatch):
        """While the breaker is open, Redis must not be contacted at all."""
        monkeypatch.setattr(tr, "_unavailable_until", float("inf"))
        monkeypatch.setattr(tr, "_revocation_redis", ExplodingRedis())

        assert tr.is_revoked("u" * 64) is False
        tr.revoke("v" * 64, 60)  # must not raise either
        assert tr.is_revoked("v" * 64) is True  # recorded in the fallback

    def test_local_fallback_respects_ttl(self, monkeypatch):
        monkeypatch.setattr(tr, "_revocation_redis", ExplodingRedis())

        tr.revoke("t" * 64, ttl_seconds=0)

        assert tr.is_revoked("t" * 64) is False

    def test_local_fallback_is_bounded(self, monkeypatch):
        """A long outage with heavy revocation traffic cannot grow it forever."""
        monkeypatch.setattr(tr, "_revocation_redis", ExplodingRedis())

        for i in range(tr._LOCAL_REVOCATIONS_MAX + 100):
            tr.revoke(f"{i:064x}", 60)

        assert len(tr._local_revocations) <= tr._LOCAL_REVOCATIONS_MAX

    def test_healthy_redis_still_detects_revocations(self, monkeypatch):
        """The fail-open path must not have disabled detection entirely."""

        class WorkingRedis:
            def __init__(self) -> None:
                self.store: set[str] = set()

            def setex(self, key: str, ttl: int, value: str) -> None:
                self.store.add(key)

            def exists(self, key: str) -> int:
                return 1 if key in self.store else 0

        monkeypatch.setattr(tr, "_revocation_redis", WorkingRedis())

        tr.revoke("t" * 64, 60)

        assert tr.is_revoked("t" * 64) is True
        assert tr.is_revoked("z" * 64) is False


class TestCredentialStuffingDetectorOutage:
    def test_record_attempt_does_not_raise_when_redis_is_down(self):
        CredentialStuffingDetector(redis_client=ExplodingRedis()).record_attempt(
            "1.2.3.4", "WrongPass1!", "user@example.com"
        )

    def test_record_attempt_fails_open_without_account(self):
        CredentialStuffingDetector(redis_client=ExplodingRedis()).record_attempt("1.2.3.4", "WrongPass1!")

    def test_detection_fails_open_during_outage(self):
        detector = CredentialStuffingDetector(redis_client=ExplodingRedis())

        assert detector.detect_stuffing("1.2.3.4", "user@example.com") is False
        assert detector.is_account_locked("user@example.com") is False
        assert detector.is_ip_flagged("1.2.3.4") is False

    def test_counts_return_zero_during_outage(self):
        detector = CredentialStuffingDetector(redis_client=ExplodingRedis())

        assert detector.get_suspicious_ip_count("1.2.3.4") == 0
        assert detector.get_suspicious_pair_count("1.2.3.4", "user@example.com") == 0
        assert detector.get_suspicious_account_count("user@example.com") == 0

    def test_login_path_singleton_fails_open_during_outage(self, monkeypatch):
        """The singleton the login endpoint actually uses must fail open too."""
        monkeypatch.setattr(csd_module.credential_stuffing_detector, "redis", ExplodingRedis())

        csd_module.credential_stuffing_detector.record_attempt("1.2.3.4", "WrongPass1!", "user@example.com")

        assert csd_module.credential_stuffing_detector.detect_stuffing("1.2.3.4", "user@example.com") is False

    def test_detection_still_locks_when_redis_is_healthy(self):
        """20 distinct password buckets from 20 IPs reach the account threshold."""
        detector = CredentialStuffingDetector(redis_client=InMemoryZSetRedis())
        threshold = 20

        for i in range(threshold):
            # Distinct first-4-char prefixes => distinct buckets per attempt.
            detector.record_attempt(f"10.0.0.{i}", f"{i:02d}ab!X", "victim@example.com")

        assert detector.is_account_locked("victim@example.com") is True
        assert detector.detect_stuffing("10.0.0.0", "victim@example.com") is False  # 1 per pair stays under
