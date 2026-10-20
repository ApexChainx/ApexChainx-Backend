"""Tests for the Stellar BlockPoller (#722, #723, #724, #726)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import fakeredis
import httpx
import pytest
from redis.exceptions import RedisError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api.v1.endpoints.block_poller import BlockPollerStatus, get_block_poller_status
from app.db.base import Base
from app.models.orm.payment import PaymentTransactionORM
from app.services import block_poller as bp
from app.services.metrics import metrics


# --------------------------------------------------------------------------- #
# Fakes / fixtures
# --------------------------------------------------------------------------- #
class FakeHorizon:
    def __init__(self, latest=None, ledgers=None, transactions=None) -> None:
        self.latest = latest
        self.ledgers = ledgers or []
        self.transactions = transactions or {}
        self.transaction_calls: list[int] = []
        self.fail_latest = False
        self.fail_ledgers = False

    def latest_ledger(self):
        if self.fail_latest:
            raise bp.HorizonError("horizon down")
        return self.latest

    def ledgers_after(self, cursor, limit):
        if self.fail_ledgers:
            raise bp.HorizonError("horizon down")
        return list(self.ledgers)[:limit]

    def transactions_for_ledger(self, sequence, limit=bp.MAX_TRANSACTIONS_PER_PAGE):
        self.transaction_calls.append(sequence)
        return list(self.transactions.get(sequence, []))


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def cache(redis_client):
    return bp.BlockPollerCache(redis_client, ttl=300)


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[PaymentTransactionORM.__table__])
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


def _ledger(sequence: int, token: str | None = None) -> bp.HorizonLedger:
    return bp.HorizonLedger(sequence=sequence, hash=f"ledger-{sequence}", paging_token=token or str(sequence))


def _tx(tx_hash: str, successful: bool = True) -> bp.HorizonTransaction:
    return bp.HorizonTransaction(hash=tx_hash, successful=successful)


def _add_payment(factory, tx_hash: str, status: str = "pending") -> None:
    with factory() as db:
        db.add(
            PaymentTransactionORM(
                id=f"pay-{tx_hash}",
                transaction_hash=tx_hash,
                type="reward",
                amount=100,
                asset_code="USDC",
                from_address="GPOOL",
                to_address="GOPS",
                status=status,
                outage_id=None,
                sla_result_id=None,
                created_at=datetime.now(UTC),
            )
        )
        db.commit()


def _payment_status(factory, tx_hash: str) -> PaymentTransactionORM:
    with factory() as db:
        row = db.query(PaymentTransactionORM).filter_by(transaction_hash=tx_hash).one()
        db.expunge(row)
        return row


def _counter_value(name: str) -> float:
    counters = metrics.get_metrics_summary()["counters"]
    return sum(value for key, value in counters.items() if key.startswith(f"{name}{{"))


# --------------------------------------------------------------------------- #
# Horizon client
# --------------------------------------------------------------------------- #
class TestStellarHorizonClient:
    def test_ledgers_and_transactions_are_parsed_and_paginated(self):
        requests: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url)
            if request.url.path == "/ledgers" and request.url.params.get("order") == "desc":
                return httpx.Response(
                    200,
                    json={
                        "_embedded": {
                            "records": [
                                {
                                    "sequence": 42,
                                    "hash": "abc",
                                    "paging_token": "42",
                                    "closed_at": "2026-10-20T12:00:00Z",
                                }
                            ]
                        }
                    },
                )
            if request.url.path == "/ledgers":
                return httpx.Response(
                    200,
                    json={
                        "_embedded": {
                            "records": [
                                {"sequence": 43, "hash": "def", "paging_token": "43"},
                            ]
                        }
                    },
                )
            if request.url.path == "/ledgers/43/transactions":
                if request.url.params.get("cursor") is None:
                    return httpx.Response(
                        200,
                        json={
                            "_embedded": {
                                "records": [
                                    {"hash": "tx-1", "successful": True, "paging_token": "t1"},
                                    {"hash": "tx-2", "successful": False, "paging_token": "t2"},
                                ]
                            }
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "_embedded": {
                            "records": [{"hash": "tx-3", "successful": True, "paging_token": "t3"}],
                        }
                    },
                )
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        horizon = bp.StellarHorizonClient(base_url="https://horizon.test", http_client=client)

        latest = horizon.latest_ledger()
        assert latest == bp.HorizonLedger(sequence=42, hash="abc", paging_token="42", closed_at="2026-10-20T12:00:00Z")

        ledgers = horizon.ledgers_after("42", limit=1)
        assert [ledger.sequence for ledger in ledgers] == [43]

        transactions = horizon.transactions_for_ledger(43, limit=2)
        assert [tx.hash for tx in transactions] == ["tx-1", "tx-2", "tx-3"]
        assert transactions[1].successful is False
        # The second page is requested with the paging token of the last row.
        assert requests[-1].params.get("cursor") == "t2"

    def test_missing_sequence_is_a_horizon_error(self):
        with pytest.raises(bp.HorizonError):
            bp._parse_ledger({"hash": "abc", "paging_token": "1"})


# --------------------------------------------------------------------------- #
# Redis cache
# --------------------------------------------------------------------------- #
class TestBlockPollerCache:
    def test_cursor_and_ledger_round_trip(self, cache, redis_client):
        assert cache.get_cursor() is None
        cache.set_cursor("123")
        assert cache.get_cursor() == "123"
        assert redis_client.ttl(cache.cursor_key) == -1  # cursor does not expire

        cache.set_ledger_transactions(7, ["a", "b"])
        assert cache.get_ledger_transactions(7) == ["a", "b"]
        assert 0 < redis_client.ttl("block_poller:ledger:7") <= 300

    def test_corrupt_ledger_cache_is_a_miss(self, cache, redis_client):
        redis_client.set("block_poller:ledger:8", "not-json")
        assert cache.get_ledger_transactions(8) is None

    def test_redis_failure_degrades_to_a_miss(self):
        class BrokenRedis:
            def get(self, *_args, **_kwargs):
                raise RedisError("redis down")

            def set(self, *_args, **_kwargs):
                raise RedisError("redis down")

            def setex(self, *_args, **_kwargs):
                raise RedisError("redis down")

        cache = bp.BlockPollerCache(BrokenRedis())
        assert cache.get_cursor() is None
        cache.set_cursor("1")  # must not raise
        assert cache.get_ledger_transactions(1) is None
        cache.set_ledger_transactions(1, ["a"])  # must not raise
        assert cache.get_last_poll() is None


# --------------------------------------------------------------------------- #
# Poll cycles
# --------------------------------------------------------------------------- #
class TestPollOnce:
    def test_cold_start_processes_latest_ledger_and_seeds_cursor(self, cache, db_factory):
        _add_payment(db_factory, "h1")
        horizon = FakeHorizon(
            latest=_ledger(200, token="200"),
            transactions={200: [_tx("h1"), _tx("h-failed", successful=False)]},
        )
        poller = bp.BlockPoller(db_factory=db_factory, horizon_client=horizon, cache=cache)

        result = poller.poll_once()

        assert result.initialized is True
        assert result.ledgers_processed == 1
        assert result.payments_confirmed == 1
        assert result.cursor == "200"
        assert cache.get_cursor() == "200"
        assert _payment_status(db_factory, "h1").status == "confirmed"
        assert _payment_status(db_factory, "h1").confirmed_at is not None

    def test_advances_only_after_reconciliation_and_confirms_matches(self, cache, db_factory):
        _add_payment(db_factory, "h1")
        _add_payment(db_factory, "h2")
        _add_payment(db_factory, "h3")
        cache.set_cursor("100")
        horizon = FakeHorizon(
            ledgers=[_ledger(101, token="101"), _ledger(102, token="102")],
            transactions={
                101: [_tx("h1"), _tx("h-miss", successful=False)],
                102: [_tx("h2")],
            },
        )
        poller = bp.BlockPoller(db_factory=db_factory, horizon_client=horizon, cache=cache)

        result = poller.poll_once()

        assert result.ledgers_processed == 2
        assert result.transactions_scanned == 3
        assert result.payments_confirmed == 2
        assert result.cursor == "102"
        assert _payment_status(db_factory, "h1").status == "confirmed"
        assert _payment_status(db_factory, "h2").status == "confirmed"
        assert _payment_status(db_factory, "h3").status == "pending"

    def test_replayed_ledgers_are_served_from_redis(self, cache, db_factory):
        _add_payment(db_factory, "h1")
        cache.set_cursor("100")
        horizon = FakeHorizon(ledgers=[_ledger(101)], transactions={101: [_tx("h1")]})
        poller = bp.BlockPoller(db_factory=db_factory, horizon_client=horizon, cache=cache)
        poller.poll_once()

        # Simulate a crash after the DB commit but before the cursor was durable.
        cache.set_cursor("100")
        horizon.transaction_calls.clear()

        result = poller.poll_once()

        assert horizon.transaction_calls == []
        assert result.cache_hits == 1
        assert result.cache_misses == 0
        assert result.payments_confirmed == 0

    def test_horizon_failure_does_not_advance_cursor(self, cache, db_factory):
        cache.set_cursor("100")
        horizon = FakeHorizon()
        horizon.fail_ledgers = True
        poller = bp.BlockPoller(db_factory=db_factory, horizon_client=horizon, cache=cache)

        result = poller.poll_once()

        assert result.error == "horizon down"
        assert result.cursor is None
        assert cache.get_cursor() == "100"

    def test_batched_update_uses_one_statement_per_batch(self, db_factory):
        for suffix in ("a", "b", "c", "d", "e"):
            _add_payment(db_factory, f"h-{suffix}")

        engine = db_factory.kw["bind"]
        statements: list[str] = []

        def record(_conn, _cursor, statement, _params, _context, _executemany):
            if statement.lstrip().upper().startswith("UPDATE PAYMENT_TRANSACTIONS"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            poller = bp.BlockPoller(
                db_factory=db_factory,
                horizon_client=FakeHorizon(),
                cache=MagicMock(),
                db_batch_size=2,
            )
            confirmed = poller._reconcile_payments(["h-a", "h-b", "h-c", "h-d", "h-e"])
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert confirmed == 5
        assert len(statements) == 3  # ceil(5 / 2)
        assert all(row.status == "confirmed" for row in (_payment_status(db_factory, f"h-{s}") for s in "abcde"))

    def test_metrics_are_recorded(self, cache, db_factory):
        _add_payment(db_factory, "h1")
        cache.set_cursor("100")
        horizon = FakeHorizon(ledgers=[_ledger(101)], transactions={101: [_tx("h1")]})
        poller = bp.BlockPoller(db_factory=db_factory, horizon_client=horizon, cache=cache)
        before = {
            name: _counter_value(name)
            for name in (
                bp.BLOCK_POLLER_POLL_TOTAL,
                bp.BLOCK_POLLER_LEDGERS_TOTAL,
                bp.BLOCK_POLLER_TRANSACTIONS_TOTAL,
                bp.BLOCK_POLLER_PAYMENTS_CONFIRMED_TOTAL,
            )
        }

        poller.poll_once()

        assert _counter_value(bp.BLOCK_POLLER_POLL_TOTAL) == before[bp.BLOCK_POLLER_POLL_TOTAL] + 1
        assert _counter_value(bp.BLOCK_POLLER_LEDGERS_TOTAL) == before[bp.BLOCK_POLLER_LEDGERS_TOTAL] + 1
        assert _counter_value(bp.BLOCK_POLLER_TRANSACTIONS_TOTAL) == before[bp.BLOCK_POLLER_TRANSACTIONS_TOTAL] + 1
        assert (
            _counter_value(bp.BLOCK_POLLER_PAYMENTS_CONFIRMED_TOTAL)
            == before[bp.BLOCK_POLLER_PAYMENTS_CONFIRMED_TOTAL] + 1
        )
        summary = metrics.get_metrics_summary()
        assert any("block_poller_last_ledger_sequence" in key for key in summary["gauges"])
        assert any("block_poller_poll_duration_ms" in key for key in summary["timers"])


# --------------------------------------------------------------------------- #
# Task + OpenAPI status endpoint
# --------------------------------------------------------------------------- #
class TestTask:
    def test_task_is_a_noop_when_disabled(self):
        from app.tasks.block_poller_tasks import poll_stellar_blocks

        with patch("app.tasks.block_poller_tasks.settings.BLOCK_POLLER_ENABLED", False):
            assert poll_stellar_blocks() == {"skipped": True, "reason": "BLOCK_POLLER_ENABLED is false"}

    def test_task_delegates_to_the_poller_when_enabled(self):
        from app.tasks.block_poller_tasks import poll_stellar_blocks

        fake_poller = MagicMock()
        fake_poller.poll_once.return_value = bp.PollResult(ledgers_processed=2, payments_confirmed=1)
        with (
            patch("app.tasks.block_poller_tasks.settings.BLOCK_POLLER_ENABLED", True),
            patch("app.services.block_poller.build_block_poller", return_value=fake_poller),
        ):
            result = poll_stellar_blocks()

        assert result["skipped"] is False
        assert result["ledgers_processed"] == 2
        assert result["payments_confirmed"] == 1


class TestStatusEndpoint:
    def test_status_reads_the_cached_poll_summary(self):
        fake_cache = MagicMock()
        fake_cache.get_last_poll.return_value = {
            "cursor": "51234567",
            "polled_at": "2026-10-20T15:30:00+00:00",
            "last_ledger_sequence": 51234567,
            "ledgers_processed": 3,
            "transactions_scanned": 42,
            "payments_confirmed": 2,
            "cache_hits": 1,
            "cache_misses": 2,
            "error": None,
        }

        status = get_block_poller_status(current_user=None, cache=fake_cache)

        assert isinstance(status, BlockPollerStatus)
        assert status.cursor == "51234567"
        assert status.last_ledger_sequence == 51234567
        assert status.last_poll_at == "2026-10-20T15:30:00+00:00"

    def test_status_is_in_the_openapi_schema(self):
        from app.main import app

        schema = app.openapi()
        assert "/api/v1/block-poller/status" in schema["paths"]
        response_schema = schema["paths"]["/api/v1/block-poller/status"]["get"]["responses"]["200"]
        assert response_schema["content"]["application/json"]["schema"]["$ref"].endswith("/BlockPollerStatus")
