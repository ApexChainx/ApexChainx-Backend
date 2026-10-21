"""TxIndexer health probe (#714).

Two layers are covered: the pure policy in ``build_indexer_health`` (status
precedence, lag maths) and the HTTP endpoint (status code mapping and the
Redis/database failure modes).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from redis.exceptions import RedisError

from app.services.tx_indexer_health import (
    IndexerState,
    TxIndexerStateStore,
    build_indexer_health,
)
from app.services.tx_indexer import FetchedPage, TxIndexer

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class InMemoryRedis:
    """Minimal stand-in for the two Redis calls the state store makes."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = False

    def get(self, key: str):
        if self.fail:
            raise RedisError("connection lost")
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        if self.fail:
            raise RedisError("connection lost")
        self.values[key] = value
        self.ttls[key] = ex
        return True


def _state(**overrides: Any) -> IndexerState:
    base = IndexerState(
        last_attempt_at=NOW - timedelta(seconds=10),
        last_success_at=NOW - timedelta(seconds=10),
        last_inserted=7,
    )
    return IndexerState(**{**base.__dict__, **overrides})


class TestStateTransitions:
    def test_success_clears_failures_and_stamps_both_timestamps(self):
        state = IndexerState(last_error="boom", consecutive_failures=3, last_inserted=0)

        updated = state.record_success(cursor="c-2", inserted=4, at=NOW)

        assert updated.last_attempt_at == NOW
        assert updated.last_success_at == NOW
        assert updated.last_error is None
        assert updated.consecutive_failures == 0
        assert updated.last_inserted == 4
        assert updated.cursor == "c-2"

    def test_failure_counts_up_and_preserves_last_success(self):
        previous = _state(consecutive_failures=1)

        updated = previous.record_failure(error="horizon 503", at=NOW)

        assert updated.consecutive_failures == 2
        assert updated.last_attempt_at == NOW
        assert updated.last_success_at == previous.last_success_at
        assert updated.last_error == "horizon 503"

    def test_long_errors_are_truncated(self):
        updated = IndexerState().record_failure(error="x" * 1000, at=NOW)

        assert updated.last_error is not None
        assert len(updated.last_error) <= 240

    def test_round_trips_through_json_shape(self):
        state = _state(consecutive_failures=2, last_error="boom", cursor="c-9")

        restored = IndexerState.from_dict(state.to_dict())

        assert restored == state

    @pytest.mark.parametrize("payload", [None, "junk", 42, [], {"consecutive_failures": -5, "last_inserted": "x"}])
    def test_corrupt_payloads_degrade_to_empty_state(self, payload: object):
        state = IndexerState.from_dict(payload)

        assert state.last_success_at is None
        assert state.consecutive_failures == 0
        assert state.last_inserted == 0


class TestStateStore:
    def test_round_trip_through_redis(self):
        redis = InMemoryRedis()
        store = TxIndexerStateStore(redis, ttl_seconds=600)  # type: ignore[arg-type]

        store.record_success(cursor="c-1", inserted=5, at=NOW)
        loaded = store.load()

        assert loaded.last_success_at == NOW
        assert loaded.last_inserted == 5
        assert redis.ttls["tx_indexer:state"] == 600

    def test_failure_after_success_keeps_success_timestamp(self):
        redis = InMemoryRedis()
        store = TxIndexerStateStore(redis, ttl_seconds=600)  # type: ignore[arg-type]
        store.record_success(cursor="c-1", inserted=5, at=NOW)

        store.record_failure(error="boom", at=NOW + timedelta(seconds=30))
        loaded = store.load()

        assert loaded.consecutive_failures == 1
        assert loaded.last_success_at == NOW

    def test_read_failure_is_fail_open(self):
        redis = InMemoryRedis()
        redis.fail = True
        store = TxIndexerStateStore(redis, ttl_seconds=600)  # type: ignore[arg-type]

        assert store.load() == IndexerState()

    def test_write_failure_does_not_raise(self):
        redis = InMemoryRedis()
        redis.fail = True
        store = TxIndexerStateStore(redis, ttl_seconds=600)  # type: ignore[arg-type]

        store.record_success(cursor="c-1", inserted=5, at=NOW)

    def test_corrupt_redis_payload_is_treated_as_empty(self):
        redis = InMemoryRedis()
        redis.values["tx_indexer:state"] = "{not json"
        store = TxIndexerStateStore(redis, ttl_seconds=600)  # type: ignore[arg-type]

        assert store.load() == IndexerState()


class TestHealthPolicy:
    def test_fresh_success_is_ok(self):
        report = build_indexer_health(_state(), now=NOW, max_lag_seconds=120)

        assert report["status"] == "ok"
        assert report["reason"] is None
        assert report["lag_seconds"] == 10.0
        assert report["components"]["database"]["status"] == "ok"

    def test_stale_success_is_degraded(self):
        report = build_indexer_health(_state(), now=NOW, max_lag_seconds=5)

        assert report["status"] == "degraded"
        assert report["reason"] == "stale"

    def test_failed_last_run_is_degraded(self):
        report = build_indexer_health(_state(consecutive_failures=1), now=NOW, max_lag_seconds=120)

        assert report["status"] == "degraded"
        assert report["reason"] == "last_run_failed"

    def test_never_run_is_degraded_not_down(self):
        report = build_indexer_health(IndexerState(), now=NOW, max_lag_seconds=120)

        assert report["status"] == "degraded"
        assert report["reason"] == "no_successful_run"
        assert report["lag_seconds"] is None
        assert report["components"]["state_store"]["status"] == "empty"

    def test_database_outage_wins_over_every_other_signal(self):
        report = build_indexer_health(
            _state(), now=NOW, max_lag_seconds=120, database_ok=False, database_error="OperationalError: down"
        )

        assert report["status"] == "down"
        assert report["reason"] == "database_unreachable"
        assert report["components"]["database"] == {
            "status": "down",
            "details": {"error": "OperationalError: down"},
        }

    def test_failure_beats_staleness_in_reporting(self):
        report = build_indexer_health(_state(consecutive_failures=4), now=NOW, max_lag_seconds=1)

        assert report["reason"] == "last_run_failed"


class TestIndexerRecordsState:
    class Recorder:
        def __init__(self) -> None:
            self.successes: list[tuple[str | None, int]] = []
            self.failures: list[str] = []

        def record_success(self, *, cursor: str | None, inserted: int) -> None:
            self.successes.append((cursor, inserted))

        def record_failure(self, *, error: str) -> None:
            self.failures.append(error)

    class Source:
        def __init__(self, records: list[dict[str, Any]]) -> None:
            self.records = records

        def fetch_payments(self, cursor: str | None, limit: int) -> FetchedPage:
            return FetchedPage(records=self.records, next_cursor="next")

        def close(self) -> None:
            pass

    class Sink:
        def upsert_many(self, transactions) -> int:
            return len(transactions)

    def test_successful_pass_is_recorded(self):
        recorder = self.Recorder()
        indexer = TxIndexer(
            self.Source([{"transaction_hash": "h1", "amount": "1", "type": "payment"}]),
            self.Sink(),
            state_recorder=recorder,
        )

        indexer.run_once(cursor="c-1")

        assert recorder.successes == [("next", 1)]
        assert recorder.failures == []

    def test_failed_pass_is_recorded_and_reraised(self):
        recorder = self.Recorder()
        indexer = TxIndexer(self.Source([{"amount": "1"}]), self.Sink(), state_recorder=recorder)

        with pytest.raises(Exception):
            indexer.run_once()

        assert recorder.successes == []
        assert len(recorder.failures) == 1


def _live_state(**overrides: Any) -> IndexerState:
    """State anchored to the real clock, for tests that go through the endpoint."""
    moment = datetime.now(UTC) - timedelta(seconds=5)
    state = IndexerState(last_attempt_at=moment, last_success_at=moment, last_inserted=7)
    return IndexerState(**{**state.__dict__, **overrides})


class TestEndpoint:
    @pytest.fixture
    def patched(self, monkeypatch):
        import app.api.v1.endpoints.indexer as endpoint

        def install(state: IndexerState, *, database_ok: bool = True) -> None:
            monkeypatch.setattr(endpoint, "get_state_store", lambda: _StoreStub(state))
            monkeypatch.setattr(
                endpoint,
                "_database_status",
                lambda: (database_ok, None if database_ok else "OperationalError: down"),
            )

        return install

    def test_ok(self, client, patched):
        patched(_live_state())

        response = client.get("/api/v1/indexer/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["last_inserted"] == 7
        assert body["components"]["database"]["status"] == "ok"

    def test_degraded_stays_200(self, client, patched):
        patched(IndexerState())

        response = client.get("/api/v1/indexer/health")

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["reason"] == "no_successful_run"

    def test_database_down_returns_503(self, client, patched):
        patched(_live_state(), database_ok=False)

        response = client.get("/api/v1/indexer/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "down"
        assert body["components"]["database"]["details"]["error"] == "OperationalError: down"


class _StoreStub:
    def __init__(self, state: IndexerState) -> None:
        self._state = state

    def load(self) -> IndexerState:
        return self._state
