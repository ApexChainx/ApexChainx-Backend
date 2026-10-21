"""TxIndexer retry-with-backoff behaviour (#713).

The backoff curve is asserted with an injected clock-free ``sleep`` and RNG, so
these tests are deterministic and instant.
"""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.services.tx_indexer import (
    FetchedPage,
    IndexedTransaction,
    RetryPolicy,
    TxIndexer,
    TxIndexerError,
    TxIndexerRetryExhausted,
    is_retryable,
    parse_horizon_record,
)


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://horizon-testnet.stellar.org/payments")
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=httpx.Response(status_code, request=request)
    )


def _record(hash_suffix: str = "a", amount: str = "12.5") -> dict[str, Any]:
    return {
        "transaction_hash": f"hash-{hash_suffix}",
        "type": "payment",
        "amount": amount,
        "asset_code": "USDC",
        "from": "GFROM",
        "to": "GTO",
        "created_at": "2026-10-01T12:00:00Z",
        "paging_token": f"token-{hash_suffix}",
    }


class FakeSource:
    """Horizon stand-in that fails a scripted number of times, then answers."""

    def __init__(self, failures: list[BaseException] | None = None, records: list[dict[str, Any]] | None = None):
        self.failures = failures or []
        self.records = records if records is not None else [_record()]
        self.calls: list[tuple[str | None, int]] = []
        self.closed = False

    def fetch_payments(self, cursor: str | None, limit: int) -> FetchedPage:
        self.calls.append((cursor, limit))
        if self.failures:
            raise self.failures.pop(0)
        return FetchedPage(records=self.records, next_cursor="token-next")

    def close(self) -> None:
        self.closed = True


class FakeSink:
    def __init__(self) -> None:
        self.batches: list[list[IndexedTransaction]] = []

    def upsert_many(self, transactions):
        self.batches.append(list(transactions))
        return len(transactions)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def policy(sleeps: list[float]) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=3,
        base_delay=0.5,
        max_delay=2.0,
        jitter_ratio=0.0,
        sleep=sleeps.append,
        rng=lambda: 0.5,
    )


class TestBackoffCurve:
    def test_delay_doubles_per_retry(self, policy: RetryPolicy):
        assert [policy.compute_delay(n) for n in (1, 2, 3)] == [0.5, 1.0, 2.0]

    def test_delay_is_capped(self, policy: RetryPolicy):
        assert policy.compute_delay(10) == 2.0

    def test_jitter_stays_within_bounds(self, sleeps: list[float]):
        low = RetryPolicy(base_delay=1.0, max_delay=100.0, jitter_ratio=0.5, sleep=sleeps.append, rng=lambda: 0.0)
        high = RetryPolicy(base_delay=1.0, max_delay=100.0, jitter_ratio=0.5, sleep=sleeps.append, rng=lambda: 1.0)

        assert low.compute_delay(1) == pytest.approx(0.5)
        assert high.compute_delay(1) == pytest.approx(1.5)

    def test_jitter_never_exceeds_max_delay(self, sleeps: list[float]):
        policy = RetryPolicy(
            max_attempts=2, base_delay=1.0, max_delay=1.0, jitter_ratio=0.5, sleep=sleeps.append, rng=lambda: 1.0
        )

        assert policy.compute_delay(1) == 1.0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_attempts": 0},
            {"base_delay": 0},
            {"base_delay": 5.0, "max_delay": 1.0},
            {"jitter_ratio": -0.1},
            {"jitter_ratio": 1.5},
        ],
    )
    def test_invalid_configuration_is_rejected(self, kwargs: dict[str, Any]):
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)

    def test_zero_attempt_is_rejected(self, policy: RetryPolicy):
        with pytest.raises(ValueError):
            policy.compute_delay(0)


class TestRetryClassification:
    @pytest.mark.parametrize("status", sorted({408, 425, 429, 500, 502, 503, 504}))
    def test_transient_statuses_are_retryable(self, status: int):
        assert is_retryable(_status_error(status)) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_client_errors_are_not_retryable(self, status: int):
        assert is_retryable(_status_error(status)) is False

    def test_transport_and_timeout_errors_are_retryable(self):
        request = httpx.Request("GET", "https://horizon-testnet.stellar.org/payments")

        assert is_retryable(httpx.ConnectError("boom", request=request)) is True
        assert is_retryable(httpx.ReadTimeout("slow", request=request)) is True

    def test_unrelated_exceptions_are_not_retryable(self):
        assert is_retryable(ValueError("nope")) is False


class TestPolicyCall:
    def test_retries_until_success_and_sleeps_between_attempts(self, policy: RetryPolicy, sleeps: list[float]):
        source = FakeSource(failures=[httpx.ConnectError("boom", request=httpx.Request("GET", "http://h"))])
        policy.rng = lambda: 0.5

        result = policy.call(lambda: source.fetch_payments(None, 10), description="fetch")

        assert result.next_cursor == "token-next"
        assert sleeps == [0.5]

    def test_exhausts_attempts_then_raises(self, sleeps: list[float]):
        failures = [httpx.ConnectError("boom", request=httpx.Request("GET", "http://h")) for _ in range(5)]
        source = FakeSource(failures=failures)
        policy = RetryPolicy(
            max_attempts=3, base_delay=0.1, max_delay=1.0, jitter_ratio=0.0, sleep=sleeps.append, rng=lambda: 0.5
        )

        with pytest.raises(TxIndexerRetryExhausted) as excinfo:
            policy.call(lambda: source.fetch_payments(None, 10), description="fetch")

        assert len(source.calls) == 3
        assert sleeps == [0.1, 0.2]
        assert isinstance(excinfo.value.__cause__, httpx.ConnectError)

    def test_non_retryable_error_is_raised_immediately(self, sleeps: list[float]):
        source = FakeSource(failures=[_status_error(400)])
        policy = RetryPolicy(max_attempts=5, base_delay=0.1, max_delay=1.0, sleep=sleeps.append)

        with pytest.raises(httpx.HTTPStatusError):
            policy.call(lambda: source.fetch_payments(None, 10), description="fetch")

        assert len(source.calls) == 1
        assert sleeps == []


class TestTxIndexerRetry:
    def test_indexer_recovers_from_transient_failures(self, sleeps: list[float]):
        source = FakeSource(failures=[_status_error(503), _status_error(429)])
        sink = FakeSink()
        indexer = TxIndexer(
            source,
            sink,
            policy=RetryPolicy(
                max_attempts=4, base_delay=0.25, max_delay=1.0, jitter_ratio=0.0, sleep=sleeps.append, rng=lambda: 0.5
            ),
            batch_size=25,
        )

        result = indexer.run_once(cursor="cursor-1")

        assert result.fetched == 1
        assert result.inserted == 1
        assert result.next_cursor == "token-next"
        assert source.calls == [("cursor-1", 25)] * 3
        assert sleeps == [0.25, 0.5]
        assert len(sink.batches) == 1

    def test_indexer_propagates_exhausted_retries(self, sleeps: list[float]):
        source = FakeSource(failures=[_status_error(503) for _ in range(10)])
        indexer = TxIndexer(
            source,
            FakeSink(),
            policy=RetryPolicy(
                max_attempts=2, base_delay=0.1, max_delay=1.0, jitter_ratio=0.0, sleep=sleeps.append, rng=lambda: 0.5
            ),
        )

        with pytest.raises(TxIndexerRetryExhausted):
            indexer.run_once()

    def test_batch_size_comes_from_settings_by_default(self, sleeps: list[float]):
        from app.core.config import settings

        indexer = TxIndexer(FakeSource(), FakeSink())

        assert indexer.batch_size == settings.TX_INDEXER_BATCH_SIZE


class TestRecordParsing:
    def test_maps_horizon_fields_into_ledger_shape(self):
        tx = parse_horizon_record(_record(amount="12.5"))

        assert tx.transaction_hash == "hash-a"
        assert tx.amount == 125_000_000  # stroops
        assert tx.asset_code == "USDC"
        assert tx.from_address == "GFROM"
        assert tx.to_address == "GTO"
        assert tx.created_at == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        assert tx.id.startswith("txidx_")

    def test_native_payments_default_to_xlm(self):
        record = _record()
        record.pop("asset_code")

        assert parse_horizon_record(record).asset_code == "XLM"

    def test_record_without_hash_is_rejected(self):
        record = _record()
        record.pop("transaction_hash")

        with pytest.raises(TxIndexerError):
            parse_horizon_record(record)

    @pytest.mark.parametrize("amount", ["not-a-number", "-1", "Infinity"])
    def test_invalid_amounts_are_rejected(self, amount: str):
        with pytest.raises(TxIndexerError):
            parse_horizon_record(_record(amount=amount))

    def test_invalid_timestamp_is_rejected(self):
        record = _record()
        record["created_at"] = "yesterday"

        with pytest.raises(TxIndexerError):
            parse_horizon_record(record)


class TestRepositoryIdempotency:
    @pytest.mark.postgres
    def test_repeated_batch_inserts_nothing_new(self, db):
        from uuid import uuid4

        from app.repositories.tx_indexer_repository import TxIndexerRepository

        tx = IndexedTransaction(
            transaction_hash=f"test-{uuid4().hex}",
            type="payment",
            amount=1_000_000,
            asset_code="USDC",
            from_address="GFROM",
            to_address="GTO",
            created_at=datetime.now(UTC),
        )
        repo = TxIndexerRepository(db)

        assert repo.upsert_many([tx]) == 1
        assert repo.upsert_many([tx]) == 0
        assert repo.upsert_many([]) == 0
