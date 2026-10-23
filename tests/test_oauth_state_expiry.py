"""#568: OAuth connect-state rows expire and are pruned.

State rows used to rely purely on the Redis TTL with no in-payload expiry:
any row that lost its TTL lived forever, and nothing swept stale rows. State
rows now carry ``expires_at``, reads expire lazily, and a periodic task prunes
the leftovers.
"""

import json
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from app.services.oauth_session import STATE_KEY_PREFIX, OAuthStateRepository


@pytest.fixture
def repo():
    return OAuthStateRepository(redis_client=fakeredis.FakeRedis())


def _store_raw(repo, state, payload, ttl=600):
    repo.redis.setex(f"{STATE_KEY_PREFIX}{state}", ttl, json.dumps(payload))


class TestExpiryStamping:
    def test_created_row_carries_expires_at(self, repo):
        state = repo.create_state("google", "http://localhost:3000/oauth/callback")
        payload = repo.get_state(state)
        assert payload is not None
        assert "expires_at" in payload
        expires_at = datetime.fromisoformat(payload["expires_at"])
        assert expires_at.tzinfo is not None
        expected = datetime.now(UTC) + timedelta(seconds=repo.ttl)
        # Allow a small scheduling skew.
        assert abs((expires_at - expected).total_seconds()) < 5

    def test_created_at_is_stamped(self, repo):
        state = repo.create_state("github", "http://localhost:3000/oauth/callback")
        payload = repo.get_state(state)
        assert datetime.fromisoformat(payload["created_at"]) <= datetime.now(UTC)


class TestLazyExpiryOnRead:
    def test_expired_row_is_not_returned_by_get_state(self, repo):
        state = repo.create_state("google", "http://localhost:3000/oauth/callback")
        payload = repo.get_state(state)
        payload["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        _store_raw(repo, state, payload)

        assert repo.get_state(state) is None
        # Lazily deleted, not just hidden.
        assert repo.redis.get(f"{STATE_KEY_PREFIX}{state}") is None

    def test_expired_row_cannot_be_consumed(self, repo):
        state = repo.create_state("google", "http://localhost:3000/oauth/callback")
        payload = repo.get_state(state)
        payload["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        _store_raw(repo, state, payload)

        assert repo.consume_state(state) is None

    def test_unexpired_row_is_returned(self, repo):
        state = repo.create_state("github", "http://localhost:3000/oauth/callback")
        assert repo.get_state(state) is not None

    def test_row_without_expires_at_relies_on_ttl(self, repo):
        """Legacy rows written before #568 must not be nuked by the read path."""
        _store_raw(
            repo,
            "legacy-state",
            {
                "provider": "google",
                "redirect_uri": "http://localhost:3000/oauth/callback",
                "code_challenge": None,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        assert repo.get_state("legacy-state") is not None

    def test_unparseable_expiry_is_treated_as_expired(self, repo):
        _store_raw(
            repo,
            "corrupt-state",
            {
                "provider": "google",
                "redirect_uri": "http://localhost:3000/oauth/callback",
                "code_challenge": None,
                "created_at": datetime.now(UTC).isoformat(),
                "expires_at": "not-a-timestamp",
            },
        )
        assert repo.get_state("corrupt-state") is None


class TestPruneExpiredStates:
    def test_prunes_only_expired_rows(self, repo):
        fresh = repo.create_state("google", "http://localhost:3000/oauth/callback")
        stale = repo.create_state("google", "http://localhost:3000/oauth/callback")

        payload = repo.get_state(stale)
        payload["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        _store_raw(repo, stale, payload)

        deleted = repo.prune_expired_states()

        assert deleted == 1
        assert repo.get_state(fresh) is not None
        assert repo.redis.get(f"{STATE_KEY_PREFIX}{stale}") is None

    def test_prunes_corrupt_rows(self, repo):
        repo.redis.setex(f"{STATE_KEY_PREFIX}corrupt", 600, "{not json")
        deleted = repo.prune_expired_states()
        assert deleted == 1
        assert repo.redis.get(f"{STATE_KEY_PREFIX}corrupt") is None

    def test_healthy_rows_survive(self, repo):
        state = repo.create_state("gitlab", "http://localhost:3000/oauth/callback")
        assert repo.prune_expired_states() == 0
        assert repo.get_state(state) is not None


class TestConsumeStillWorks:
    def test_consume_is_one_shot(self, repo):
        state = repo.create_state("google", "http://localhost:3000/oauth/callback")
        first = repo.consume_state(state)
        second = repo.consume_state(state)
        assert first is not None
        assert second is None

    def test_pkce_verification_unchanged(self, repo):
        import base64
        import hashlib

        verifier = "correct-horse-battery-staple"
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        )
        assert OAuthStateRepository.verify_code_challenge(verifier, challenge) is True
        assert OAuthStateRepository.verify_code_challenge("wrong", challenge) is False
