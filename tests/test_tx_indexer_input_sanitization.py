"""Input sanitization tests for TxIndexer — Issue #711.

The sanitizer is allow-list based, so the tests are written as a table of
hostile and sloppy inputs with the one canonical value that may survive. They
cover both halves of the surface: query filters on the read API, and records
ingested from Horizon.
"""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.security import require_engineer
from app.db.session import get_db
from app.main import app
from app.repositories.tx_indexer_repository import TxIndexerRepository
from app.services.tx_indexer import IndexedTransaction, TxIndexerRecordError
from app.services.tx_indexer_input import (
    TxIndexFilters,
    TxIndexerValidationError,
    sanitize_account,
    sanitize_asset_code,
    sanitize_bound,
    sanitize_operation_type,
    sanitize_status,
    sanitize_transaction_hash,
)

HASH = "9c1f4f2f0f5b4a1d8c3e2b7a6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c"


# ── Hash ────────────────────────────────────────────────────────────────


class TestTransactionHash:
    def test_accepts_a_canonical_hash(self):
        assert sanitize_transaction_hash(HASH) == HASH

    def test_normalizes_case(self):
        assert sanitize_transaction_hash(HASH.upper()) == HASH

    def test_strips_surrounding_whitespace(self):
        assert sanitize_transaction_hash(f"  {HASH}  ") == HASH

    def test_missing_value_is_not_an_error(self):
        assert sanitize_transaction_hash(None) is None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "abc123",
            HASH[:-1],
            HASH + "0",
            HASH[:-1] + "g",
            "'; DROP TABLE payment_transactions; --",
            "0" * 64 + "'",
        ],
    )
    def test_rejects_anything_but_64_hex_characters(self, value):
        with pytest.raises(TxIndexerValidationError) as exc_info:
            sanitize_transaction_hash(value)
        assert exc_info.value.field == "transaction_hash"

    def test_rejects_control_characters(self):
        with pytest.raises(TxIndexerValidationError, match="control"):
            sanitize_transaction_hash("abc\x00" + "0" * 60)

    def test_rejects_embedded_newline(self):
        with pytest.raises(TxIndexerValidationError, match="control"):
            sanitize_transaction_hash("a" * 32 + "\n" + "b" * 31)


# ── Account ─────────────────────────────────────────────────────────────


class TestAccount:
    def test_strips_whitespace(self):
        assert sanitize_account("  GABCDEF  ") == "GABCDEF"

    def test_accepts_placeholders_used_by_seed_data(self):
        assert sanitize_account("SYSTEM_POOL") == "SYSTEM_POOL"

    def test_accepts_muxed_ids(self):
        assert sanitize_account("GABCDEF:12345") == "GABCDEF:12345"

    @pytest.mark.parametrize(
        "value",
        [
            "G' OR 1=1 --",
            "GABCDEF; DROP TABLE payment_transactions",
            "GABCDEF--",
            "G ABCDEF",
            "GABCDEF\x00",
            "G" * 70,
        ],
    )
    def test_rejects_quotes_comments_and_oversized_values(self, value):
        with pytest.raises(TxIndexerValidationError):
            sanitize_account(value)

    def test_empty_is_rejected(self):
        with pytest.raises(TxIndexerValidationError, match="empty"):
            sanitize_account("   ")


# ── Asset code, status, type ────────────────────────────────────────────


class TestTokenFields:
    def test_asset_code_is_upper_cased(self):
        assert sanitize_asset_code("usdc") == "USDC"

    def test_asset_code_rejects_punctuation(self):
        with pytest.raises(TxIndexerValidationError):
            sanitize_asset_code("USD'C")

    def test_asset_code_rejects_over_twelve_characters(self):
        with pytest.raises(TxIndexerValidationError):
            sanitize_asset_code("A" * 13)

    def test_status_is_lower_cased(self):
        assert sanitize_status("CONFIRMED") == "confirmed"

    def test_status_rejects_sql_payload(self):
        with pytest.raises(TxIndexerValidationError):
            sanitize_status("confirmed' OR '1'='1")

    def test_operation_type_is_lower_cased(self):
        assert sanitize_operation_type("Path_Payment_Strict_Send") == "path_payment_strict_send"

    def test_operation_type_rejects_spaces(self):
        with pytest.raises(TxIndexerValidationError):
            sanitize_operation_type("pay ment")


# ── Time bounds ─────────────────────────────────────────────────────────


class TestTimeBounds:
    def test_naive_timestamps_are_treated_as_utc(self):
        bound = sanitize_bound(datetime(2026, 10, 20, 12, 0), field="created_after")
        assert bound == datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    def test_offsets_are_converted_to_utc(self):
        bound = sanitize_bound(
            datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=5))), field="created_after"
        )
        assert bound == datetime(2026, 10, 20, 7, 0, tzinfo=UTC)

    def test_missing_bound_stays_none(self):
        assert sanitize_bound(None, field="created_after") is None


# ── Aggregate filter object ─────────────────────────────────────────────


class TestTxIndexFilters:
    def test_returns_canonical_values(self):
        filters = TxIndexFilters.from_query(
            transaction_hash=HASH.upper(),
            account=" GABCDEF ",
            asset_code="usdc",
            status="CONFIRMED",
            transaction_type="PAYMENT",
        )

        assert filters.transaction_hash == HASH
        assert filters.account == "GABCDEF"
        assert filters.asset_code == "USDC"
        assert filters.status == "confirmed"
        assert filters.transaction_type == "payment"

    def test_absent_filters_stay_none(self):
        filters = TxIndexFilters.from_query()
        assert filters == TxIndexFilters()

    def test_first_invalid_value_wins(self):
        with pytest.raises(TxIndexerValidationError) as exc_info:
            TxIndexFilters.from_query(asset_code="USDC", account="G' OR 1=1 --")
        assert exc_info.value.field == "account"


# ── Ingest path ─────────────────────────────────────────────────────────


def _record(**overrides):
    record = {
        "transaction_hash": HASH,
        "type": "payment",
        "amount": "10.0000000",
        "asset_code": "USDC",
        "from": "GAAAA",
        "to": "GBBBB",
        "created_at": "2026-10-20T12:00:00Z",
    }
    record.update(overrides)
    return record


class TestIngestSanitization:
    def test_hash_is_normalized_to_lower_case(self):
        transaction = IndexedTransaction.from_horizon_record(_record(transaction_hash=HASH.upper()))
        assert transaction.transaction_hash == HASH
        assert transaction.id == f"idx_{HASH}"

    def test_asset_code_is_normalized(self):
        transaction = IndexedTransaction.from_horizon_record(_record(asset_code="usdc"))
        assert transaction.asset_code == "USDC"

    def test_operation_type_is_normalized(self):
        transaction = IndexedTransaction.from_horizon_record(_record(type="Payment"))
        assert transaction.type == "payment"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"transaction_hash": "not-a-hash"},
            {"transaction_hash": HASH[:-1]},
            {"transaction_hash": "' OR 1=1 --"},
            {"asset_code": "USDC'"},
            {"asset_code": "A" * 13},
            {"type": "pay ment"},
            {"from": "G' OR 1=1 --"},
            {"to": "G;DROP TABLE payment_transactions"},
            {"amount": "922337203685478000000000000"},
        ],
    )
    def test_unsafe_records_are_rejected(self, overrides):
        with pytest.raises(TxIndexerRecordError):
            IndexedTransaction.from_horizon_record(_record(**overrides))


# ── HTTP surface ────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(role="engineer", email="eng@example.com")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def repo_calls(monkeypatch):
    calls: dict[str, object] = {}

    def fake_list(self, **kwargs):
        calls["list"] = kwargs
        return []

    def fake_get(self, transaction_hash):
        calls["get"] = {"transaction_hash": transaction_hash}
        return None

    monkeypatch.setattr(TxIndexerRepository, "list_indexed", fake_list)
    monkeypatch.setattr(TxIndexerRepository, "get_by_hash", fake_get)
    return calls


class TestReadApiSanitization:
    def test_sql_payload_in_account_is_a_422(self, client, repo_calls):
        response = client.get("/api/v1/indexer/transactions", params={"account": "G' OR 1=1 --"})

        assert response.status_code == 422
        assert "account" in response.json()["detail"]
        assert "list" not in repo_calls

    def test_control_characters_are_a_422(self, client, repo_calls):
        response = client.get("/api/v1/indexer/transactions", params={"type": "pay\nment"})

        assert response.status_code == 422
        assert "list" not in repo_calls

    def test_filters_reach_the_repository_in_canonical_form(self, client, repo_calls):
        response = client.get(
            "/api/v1/indexer/transactions",
            params={"transaction_hash": HASH.upper(), "asset_code": "usdc", "status": "CONFIRMED"},
        )

        assert response.status_code == 200
        forwarded = repo_calls["list"]
        assert forwarded["transaction_hash"] == HASH
        assert forwarded["asset_code"] == "USDC"
        assert forwarded["status"] == "confirmed"

    def test_non_hex_hash_filter_is_a_422(self, client, repo_calls):
        response = client.get("/api/v1/indexer/transactions", params={"transaction_hash": "abc123"})

        assert response.status_code == 422
        assert "list" not in repo_calls

    def test_path_hash_is_sanitized_before_lookup(self, client, repo_calls):
        response = client.get("/api/v1/indexer/transactions/not-a-real-hash")

        assert response.status_code == 422
        assert "get" not in repo_calls

    def test_uppercase_path_hash_is_normalized(self, client, repo_calls):
        response = client.get(f"/api/v1/indexer/transactions/{HASH.upper()}")

        assert response.status_code == 404
        assert repo_calls["get"] == {"transaction_hash": HASH}
