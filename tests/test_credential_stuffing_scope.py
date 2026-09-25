"""Tests for #507: credential-stuffing lockouts scoped to (IP, account).

The detector used to lock on the source IP alone, so an attacker spraying from
a shared office NAT locked out every legitimate user behind that address for
``AUTH_LOCKOUT_DURATION_MINUTES * 4``.  These tests use an in-memory stand-in
for Redis sorted sets, so no services are required.
"""

import pytest

from app.core.config import settings
from app.services.credential_stuffing_detector import CredentialStuffingDetector


class FakeRedis:
    """Enough of the Redis sorted-set API for the detector."""

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


@pytest.fixture
def detector() -> CredentialStuffingDetector:
    return CredentialStuffingDetector(redis_client=FakeRedis())


def _spray(detector, ip, account, count):
    # The detector buckets on the first four characters, so each guess needs a
    # distinct four-character prefix to look like a new entry.
    for i in range(count):
        detector.record_attempt(ip, f"{i:04d}-attempted-password", account)


class TestSharedIpIsolation:
    def test_attacker_spray_does_not_lock_a_different_account(self, detector):
        """The core regression: account A is sprayed, account B still passes."""
        _spray(detector, "203.0.113.10", "victim@example.com", settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD + 5)

        assert detector.detect_stuffing("203.0.113.10", "victim@example.com") is True
        # Same address, different account: must not be locked.
        assert detector.detect_stuffing("203.0.113.10", "colleague@example.com") is False
        assert detector.is_account_locked("colleague@example.com") is False

    def test_spray_from_many_ips_against_one_account_locks_that_account(self, detector):
        """Distributed attack: no single IP trips, the account scope must."""
        per_ip = settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD - 5
        for i in range(6):
            _spray(detector, f"198.51.100.{i}", "target@example.com", per_ip)

        # No individual pair reached the threshold...
        assert detector.detect_stuffing("198.51.100.0", "target@example.com") is False
        # ...but the account as a whole did.
        assert detector.is_account_locked("target@example.com") is True

    def test_untouched_account_is_never_locked(self, detector):
        _spray(detector, "203.0.113.10", "victim@example.com", 50)
        assert detector.is_account_locked("unrelated@example.com") is False


class TestIpSignalIsAlertingOnly:
    def test_ip_wide_spray_is_flagged(self, detector):
        # 25 distinct accounts, a few guesses each, from one address.
        for account in range(settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD + 5):
            _spray(detector, "192.0.2.7", f"user{account}@example.com", 2)
        assert detector.is_ip_flagged("192.0.2.7") is True

    def test_ip_flag_does_not_imply_any_account_is_locked(self, detector):
        for account in range(settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD + 5):
            _spray(detector, "192.0.2.7", f"user{account}@example.com", 2)
        assert detector.is_ip_flagged("192.0.2.7") is True
        assert detector.is_account_locked("user0@example.com") is False
        assert detector.detect_stuffing("192.0.2.7", "user0@example.com") is False


class TestKeyHygiene:
    def test_account_identity_is_hashed_into_the_key(self, detector):
        _spray(detector, "192.0.2.7", "sensitive@example.com", 1)
        assert not any("sensitive@example.com" in key for key in detector.redis.zsets)
        assert any(key.startswith("cred_stuffing:pair:192.0.2.7:") for key in detector.redis.zsets)
        assert any(key.startswith("cred_stuffing:account:") for key in detector.redis.zsets)

    def test_email_case_and_whitespace_do_not_create_a_second_bucket(self, detector):
        _spray(detector, "192.0.2.7", "user@example.com", 1)
        detector.record_attempt("192.0.2.7", "zzzz-attempted-password", "  USER@Example.com ")
        pair_keys = [k for k in detector.redis.zsets if k.startswith("cred_stuffing:pair:")]
        assert len(pair_keys) == 1

    def test_every_written_key_gets_a_ttl(self, detector):
        _spray(detector, "192.0.2.7", "user@example.com", 3)
        for key in detector.redis.zsets:
            assert key in detector.redis.expiries

    def test_windows_expire(self, detector):
        _spray(detector, "192.0.2.7", "user@example.com", 3)
        ttl = detector.redis.expiries["cred_stuffing:account:" + CredentialStuffingDetector._account_hash("user@example.com")]
        assert ttl >= settings.AUTH_CREDENTIAL_STUFFING_WINDOW_MINUTES * 60


class TestLockoutPolicyCap:
    def test_default_multiplier_is_capped(self):
        # 15 * 4 = 60, exactly the cap.
        assert CredentialStuffingDetector.lockout_minutes() == 60

    def test_cap_holds_when_the_base_lockout_grows(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_LOCKOUT_DURATION_MINUTES", 120, raising=False)
        assert CredentialStuffingDetector.lockout_minutes() == settings.AUTH_STUFFING_LOCKOUT_MAX_MINUTES

    def test_short_base_lockout_is_not_inflated_to_the_cap(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_LOCKOUT_DURATION_MINUTES", 5, raising=False)
        assert CredentialStuffingDetector.lockout_minutes() == 20


class TestDetectionWithoutAccount:
    def test_legacy_call_signature_still_records_the_ip_scope(self, detector):
        _spray(detector, "192.0.2.7", None, settings.AUTH_LOCKOUT_ENTROPY_THRESHOLD + 1)
        assert detector.detect_stuffing("192.0.2.7") is True
        assert detector.is_ip_flagged("192.0.2.7") is True

    def test_no_account_means_no_pair_or_account_keys(self, detector):
        _spray(detector, "192.0.2.7", None, 3)
        assert all(k.startswith("cred_stuffing:ip:") for k in detector.redis.zsets)
        assert detector.is_account_locked("") is False
