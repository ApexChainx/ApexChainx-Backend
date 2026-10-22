"""Tests for the TxIndexer ingest path — Issue #710.

The ingest path has three contracts worth pinning down:

* Horizon records are normalized into the ledger of record exactly once, with
  amounts converted to stroops and a stable id derived from the hash;
* records that cannot be keyed or scaled are skipped and counted, never
  written;
* a pass advances the cursor and is idempotent on re-delivery.

No network access is involved: the Horizon transport is a ``httpx.MockTransport``
and the sink is a fake.
"""

from datetime import UTC, datetime

import httpx
import pytest

from app.services.tx_indexer import (
    HORIZON_MAX_PAGE_SIZE,
    HorizonClient,
    IndexedTransaction,
    TxIndexer,
    TxIndexerRecordError,
    TxIndexerTransportError,
)

HASH = "9c1f4f2f0f5b4a1d8c3e2b7a6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c"


def _record(**overrides):
    record = {
        "transaction_hash": HASH,
        "type": "payment",
        "amount": "10.0000000",
        "asset_code": "USDC",
        "from": "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
        "to": "GBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBXY",
        "created_at": "2026-10-20T12:00:00Z",
        "paging_token": "100",
    }
    record.update(overrides)
    return record


class FakeSink:
    """Records every batch handed to it and reports how many rows were new."""

    def __init__(self, stored: int | None = None) -> None:
        self.batches: list[list[IndexedTransaction]] = []
        self._stored = stored

    def upsert_many(self, transactions):
        batch = list(transactions)
        self.batches.append(batch)
        return len(batch) if self._stored is None else self._stored


class FakeClient:
    """Serves pre-canned pages and remembers the cursor it was handed."""

    def __init__(self, pages: list[tuple[list[dict], str | None]]) -> None:
        self._pages = list(pages)
        self.calls: list[tuple[str | None, int]] = []

    def fetch_payments(self, cursor, limit):
        self.calls.append((cursor, limit))
        if not self._pages:
            return [], cursor
        return self._pages.pop(0)


# ── Normalization ───────────────────────────────────────────────────────


class TestFromHorizonRecord:
    def test_converts_decimal_amount_to_stroops(self):
        transaction = IndexedTransaction.from_horizon_record(_record())
        assert transaction.amount == 100_000_000

    def test_converts_fractional_stroop_amount(self):
        transaction = IndexedTransaction.from_horizon_record(_record(amount="0.0000001"))
        assert transaction.amount == 1

    def test_id_is_derived_from_the_hash(self):
        transaction = IndexedTransaction.from_horizon_record(_record())
        assert transaction.id == f"idx_{HASH}"
        assert transaction.transaction_hash == HASH

    def test_created_at_is_timezone_aware_utc(self):
        transaction = IndexedTransaction.from_horizon_record(_record())
        assert transaction.created_at == datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
        assert transaction.created_at.tzinfo is not None

    def test_native_payment_gets_xlm_asset_code(self):
        transaction = IndexedTransaction.from_horizon_record(_record(asset_code=None))
        assert transaction.asset_code == "XLM"

    def test_indexed_rows_are_confirmed(self):
        transaction = IndexedTransaction.from_horizon_record(_record())
        assert transaction.status == "confirmed"

    def test_account_pairs_fall_back_to_operation_specific_fields(self):
        transaction = IndexedTransaction.from_horizon_record(
            _record(
                type="create_account",
                **{"from": None, "to": None},
                funder="GFUNDER",
                account="GACCOUNT",
            )
        )
        assert transaction.from_address == "GFUNDER"
        assert transaction.to_address == "GACCOUNT"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"transaction_hash": None},
            {"transaction_hash": ""},
            {"amount": None},
            {"amount": "not-a-number"},
            {"amount": "-1.0000000"},
            {"created_at": None},
            {"created_at": "not-a-date"},
        ],
    )
    def test_rejects_unusable_records(self, overrides):
        with pytest.raises(TxIndexerRecordError):
            IndexedTransaction.from_horizon_record(_record(**overrides))

    def test_rejects_missing_account_pair(self):
        with pytest.raises(TxIndexerRecordError, match="from/to"):
            IndexedTransaction.from_horizon_record(_record(**{"from": None, "to": None}))


# ── Ingest pass ─────────────────────────────────────────────────────────


class TestRunOnce:
    def test_stores_normalized_page_and_advances_cursor(self):
        sink = FakeSink()
        client = FakeClient([([_record()], "100")])
        result = TxIndexer(client, sink, batch_size=10).run_once(cursor="99")

        assert result.fetched == 1
        assert result.stored == 1
        assert result.skipped == 0
        assert result.cursor == "100"
        assert client.calls == [("99", 10)]
        assert sink.batches[0][0].transaction_hash == HASH

    def test_collapses_operations_that_share_a_transaction(self):
        sink = FakeSink()
        second_op = _record(paging_token="101")
        client = FakeClient([([_record(), second_op], "101")])

        result = TxIndexer(client, sink, batch_size=10).run_once()

        assert result.fetched == 2
        assert result.stored == 1
        assert len(sink.batches[0]) == 1
        assert result.cursor == "101"

    def test_skips_unusable_records_without_calling_the_sink(self):
        sink = FakeSink()
        client = FakeClient([([_record(amount=None)], "100")])

        result = TxIndexer(client, sink, batch_size=10).run_once()

        assert result.fetched == 1
        assert result.stored == 0
        assert result.skipped == 1
        assert sink.batches == []
        assert result.cursor == "100"

    def test_empty_page_keeps_the_incoming_cursor(self):
        sink = FakeSink()
        client = FakeClient([([], None)])

        result = TxIndexer(client, sink, batch_size=10).run_once(cursor="500")

        assert result.fetched == 0
        assert result.cursor == "500"
        assert sink.batches == []

    def test_reports_rows_already_indexed(self):
        sink = FakeSink(stored=0)
        client = FakeClient([([_record()], "100")])

        result = TxIndexer(client, sink, batch_size=10).run_once()

        assert result.fetched == 1
        assert result.stored == 0

    def test_batch_size_is_bounded_by_the_horizon_page_cap(self):
        with pytest.raises(ValueError):
            TxIndexer(FakeClient([]), FakeSink(), batch_size=0)
        with pytest.raises(ValueError):
            TxIndexer(FakeClient([]), FakeSink(), batch_size=HORIZON_MAX_PAGE_SIZE + 1)


# ── Horizon transport ───────────────────────────────────────────────────


def _client(handler) -> HorizonClient:
    return HorizonClient(
        "https://horizon.test",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://horizon.test"),
    )


class TestHorizonClient:
    def test_returns_records_and_next_cursor(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["order"] == "asc"
            assert request.url.params["limit"] == "2"
            assert "cursor" not in request.url.params
            return httpx.Response(200, json={"_embedded": {"records": [_record(), _record(paging_token="101")]}})

        records, next_cursor = _client(handler).fetch_payments(None, 2)

        assert len(records) == 2
        assert next_cursor == "101"

    def test_sends_cursor_when_resuming(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["cursor"] = request.url.params["cursor"]
            return httpx.Response(200, json={"_embedded": {"records": []}})

        _client(handler).fetch_payments("42", 5)

        assert seen["cursor"] == "42"

    def test_error_status_raises_transport_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={})

        with pytest.raises(TxIndexerTransportError, match="503"):
            _client(handler).fetch_payments(None, 5)

    def test_connection_failure_raises_transport_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        with pytest.raises(TxIndexerTransportError):
            _client(handler).fetch_payments(None, 5)

    def test_empty_base_url_is_rejected(self):
        with pytest.raises(ValueError):
            HorizonClient("")
