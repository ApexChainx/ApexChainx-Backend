"""Regression tests for #576: completed Idempotency-Key records must not
grow without bound.

Two independent safeguards are covered:

1. TTL (added under #16): a completed key already carries an expiry, so a
   client that reuses a key long after it finished is treated as a fresh
   request rather than replaying (or blocking on) a stale record.
2. Cap (#576): a sorted-set index of completed keys is trimmed down to
   ``IDEMPOTENCY_MAX_COMPLETED_KEYS`` on every write, so a burst of unique
   keys inside a single TTL window can't grow the keystore past the cap
   before anything has a chance to expire naturally.

Both are tested against a small in-memory Redis stand-in with a
manually-advanceable clock, so nothing here depends on real wall-clock time.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.middleware.idempotency import IdempotencyMiddleware

_INDEX_KEY = "idempotency:completed-index"


class FakeRedis:
    """In-memory Redis stand-in that actually models TTL expiry and sorted
    sets, driven by a manually-advanceable fake clock (``self.now``).

    This is intentionally a superset of the ``FakeRedis`` used in
    ``test_idempotency_malformed_body.py`` -- it adds real expiry and the
    handful of sorted-set commands the #576 cap logic needs.
    """

    def __init__(self) -> None:
        self.now: float = 1_000_000.0
        self._store: dict[str, tuple[str, float | None]] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self.setex_calls: list[tuple[str, int]] = []

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward, expiring anything past its TTL."""
        self.now += seconds

    def _expire_if_due(self, key: str) -> None:
        entry = self._store.get(key)
        if entry is not None and entry[1] is not None and entry[1] <= self.now:
            del self._store[key]

    def get(self, key: str):
        self._expire_if_due(key)
        entry = self._store.get(key)
        return entry[0] if entry else None

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl))
        self._store[key] = (value, self.now + ttl)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def zadd(self, name: str, mapping: dict) -> None:
        self._zsets.setdefault(name, {}).update(mapping)

    def zcard(self, name: str) -> int:
        return len(self._zsets.get(name, {}))

    def zrange(self, name: str, start: int, end: int) -> list[str]:
        members = sorted(self._zsets.get(name, {}).items(), key=lambda kv: kv[1])
        keys = [member for member, _ in members]
        if end == -1:
            return keys[start:]
        return keys[start : end + 1]

    def zrem(self, name: str, *members: str) -> None:
        z = self._zsets.get(name, {})
        for member in members:
            z.pop(member, None)


def _client(redis: FakeRedis, max_completed_keys: int | None = None) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        IdempotencyMiddleware,
        redis_client=redis,
        max_completed_keys=max_completed_keys,
    )

    calls = {"n": 0}

    @app.post("/api/v1/increment")
    async def increment(payload: dict):
        calls["n"] += 1
        return {"calls": calls["n"]}

    client = TestClient(app, raise_server_exceptions=False)
    client.calls = calls  # type: ignore[attr-defined]
    return client


class TestCompletedKeyTTL:
    def test_ttl_written_matches_configured_setting(self):
        redis = FakeRedis()
        client = _client(redis)

        resp = client.post("/api/v1/increment", json={}, headers={"Idempotency-Key": "key-1"})
        assert resp.status_code == 200
        assert redis.setex_calls, "expected the completed response to be cached"
        _, ttl = redis.setex_calls[-1]
        assert ttl == settings.IDEMPOTENCY_KEY_TTL_HOURS * 3600

    def test_retry_within_ttl_window_still_dedupes(self):
        redis = FakeRedis()
        client = _client(redis)
        headers = {"Idempotency-Key": "key-1"}

        first = client.post("/api/v1/increment", json={}, headers=headers)
        assert first.json() == {"calls": 1}

        # Well within the TTL window -- must replay, not re-execute.
        redis.advance(60)
        replay = client.post("/api/v1/increment", json={}, headers=headers)
        assert replay.json() == {"calls": 1}
        assert client.calls["n"] == 1

    def test_key_expires_and_is_reprocessed_after_ttl(self):
        redis = FakeRedis()
        client = _client(redis)
        headers = {"Idempotency-Key": "key-1"}

        first = client.post("/api/v1/increment", json={}, headers=headers)
        assert first.json() == {"calls": 1}

        # Push the fake clock past the configured TTL.
        redis.advance(settings.IDEMPOTENCY_KEY_TTL_HOURS * 3600 + 1)

        second = client.post("/api/v1/increment", json={}, headers=headers)
        assert second.json() == {"calls": 2}, (
            "a key reused after its TTL has passed must be treated as a "
            "fresh request, proving the completed record does not live "
            "forever"
        )
        assert client.calls["n"] == 2


class TestCompletedKeyCap:
    def test_index_never_grows_past_the_configured_cap(self):
        redis = FakeRedis()
        client = _client(redis, max_completed_keys=3)

        for i in range(10):
            resp = client.post(
                "/api/v1/increment",
                json={},
                headers={"Idempotency-Key": f"key-{i}"},
            )
            assert resp.status_code == 200
            # The cap is enforced on every write, so it must never be
            # exceeded even mid-burst, not just once traffic settles.
            assert redis.zcard(_INDEX_KEY) <= 3

        assert redis.zcard(_INDEX_KEY) == 3

    def test_eviction_removes_the_oldest_keys_first(self):
        redis = FakeRedis()
        client = _client(redis, max_completed_keys=2)

        for i in range(4):
            client.post(
                "/api/v1/increment",
                json={},
                headers={"Idempotency-Key": f"key-{i}"},
            )

        # Only the two most recent keys should still be cached.
        assert redis.get("idempotency::key-0") is None
        assert redis.get("idempotency::key-1") is None
        assert redis.get("idempotency::key-2") is not None
        assert redis.get("idempotency::key-3") is not None

    def test_dedupe_still_works_for_a_key_that_was_not_evicted(self):
        redis = FakeRedis()
        client = _client(redis, max_completed_keys=2)
        headers = {"Idempotency-Key": "key-keep"}

        first = client.post("/api/v1/increment", json={}, headers=headers)
        assert first.json() == {"calls": 1}

        # Cause eviction pressure with unrelated keys, but stay within the
        # cap so "key-keep" is never the oldest entry.
        client.post("/api/v1/increment", json={}, headers={"Idempotency-Key": "key-other"})

        replay = client.post("/api/v1/increment", json={}, headers=headers)
        assert replay.json() == {"calls": 1}, "a key still within the cap must keep deduping correctly"
        assert client.calls["n"] == 2  # key-keep once, key-other once
