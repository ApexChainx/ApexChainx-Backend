"""SQL-injection hardening for the TxIndexer query path (#716).

The interesting property is not "the endpoint returns 422 for a payload with a
quote in it" — it is that no user value can reach SQL as text at all: sort keys
resolve through a whitelist, and every filter is bound as a statement
parameter. These tests assert both the validation layer and the compiled
statement.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.security import require_engineer
from app.main import app
from app.models.orm.payment import PaymentTransactionORM
from app.repositories.tx_indexer_repository import TxIndexerRepository
from app.services.tx_indexer import IndexedTransaction
from app.services.tx_indexer_query import (
    DEFAULT_LIMIT,
    escape_like,
    validate_account,
    validate_amount,
    validate_amount_range,
    validate_asset_code,
    validate_limit,
    validate_outage_id,
    validate_sort,
    validate_status,
    validate_timestamp_range,
    validate_transaction_hash,
    validate_transaction_type,
    TxIndexerQueryError,
)

INJECTION = "'; DROP TABLE payment_transactions; --"


class TestTransactionHashValidation:
    def test_accepts_and_lowercases_a_real_hash(self):
        assert validate_transaction_hash("A" * 64) == "a" * 64

    @pytest.mark.parametrize("raw", [INJECTION, "not-a-hash", "a" * 63, "a" * 65, "g" * 64, "", "  "])
    def test_rejects_anything_else(self, raw: str):
        with pytest.raises(TxIndexerQueryError):
            validate_transaction_hash(raw)

    def test_absent_filter_is_none(self):
        assert validate_transaction_hash(None) is None


class TestAccountValidation:
    VALID = "G" + "A" * 55

    def test_accepts_a_stellar_public_key(self):
        assert validate_account(self.VALID) == self.VALID

    @pytest.mark.parametrize("raw", [INJECTION, "G" + "1" * 55, "x" * 56, "G" * 10])
    def test_rejects_non_keys(self, raw: str):
        with pytest.raises(TxIndexerQueryError):
            validate_account(raw)


class TestEnumAndIdValidation:
    def test_asset_code_is_upper_cased(self):
        assert validate_asset_code("usdc") == "USDC"

    @pytest.mark.parametrize("raw", [INJECTION, "USD C", "USDC-1", "x" * 13])
    def test_asset_code_rejects_metacharacters(self, raw: str):
        with pytest.raises(TxIndexerQueryError):
            validate_asset_code(raw)

    @pytest.mark.parametrize("status", ["pending", "confirmed", "failed", "CONFIRMED"])
    def test_known_statuses_pass(self, status: str):
        assert validate_status(status) == status.lower()

    @pytest.mark.parametrize("raw", [INJECTION, "settled", "confirmed' OR '1'='1"])
    def test_unknown_statuses_fail(self, raw: str):
        with pytest.raises(TxIndexerQueryError):
            validate_status(raw)

    def test_type_accepts_internal_labels(self):
        assert validate_transaction_type("sla_payment") == "sla_payment"
        assert validate_transaction_type("Payment") == "payment"

    @pytest.mark.parametrize("raw", [INJECTION, "pay ment", "payment;"])
    def test_type_rejects_anything_else(self, raw: str):
        with pytest.raises(TxIndexerQueryError):
            validate_transaction_type(raw)

    def test_outage_id_accepts_generated_ids(self):
        assert validate_outage_id("outage_8f2c1d") == "outage_8f2c1d"

    @pytest.mark.parametrize("raw", [INJECTION, "outage_1 OR 1=1", "out age"])
    def test_outage_id_rejects_unsupported_characters(self, raw: str):
        with pytest.raises(TxIndexerQueryError):
            validate_outage_id(raw)


class TestAmountAndLimitValidation:
    def test_amounts_coerce_from_strings(self):
        assert validate_amount("250", field="min_amount") == 250

    @pytest.mark.parametrize("raw", ["12.5", "abc", INJECTION, 2**63, -(2**63) - 1, True])
    def test_amounts_must_be_bounded_integers(self, raw):
        with pytest.raises(TxIndexerQueryError):
            validate_amount(raw, field="min_amount")

    def test_inverted_range_is_rejected(self):
        with pytest.raises(TxIndexerQueryError):
            validate_amount_range(500, 100)

    def test_limit_defaults_and_bounds(self):
        assert validate_limit(None) == DEFAULT_LIMIT
        assert validate_limit(200) == 200
        for bad in (0, 201, "10", True):
            with pytest.raises(TxIndexerQueryError):
                validate_limit(bad)


class TestSortWhitelist:
    def test_known_keys_pass_through(self):
        assert validate_sort("amount", "ASC") == ("amount", "asc")

    def test_defaults(self):
        assert validate_sort(None, None) == ("created_at", "desc")

    @pytest.mark.parametrize(
        "raw_sort",
        [INJECTION, "amount; SELECT pg_sleep(10)", "payment_transactions.amount", "unknown"],
    )
    def test_arbitrary_columns_are_rejected(self, raw_sort: str):
        with pytest.raises(TxIndexerQueryError):
            validate_sort(raw_sort, "asc")

    @pytest.mark.parametrize("raw_dir", ["asc; --", "sideways", ""])
    def test_arbitrary_directions_are_rejected(self, raw_dir: str):
        with pytest.raises(TxIndexerQueryError):
            validate_sort("created_at", raw_dir)


class TestTimestampAndLikeHelpers:
    def test_inverted_window_is_rejected(self):
        start, end = datetime.now(UTC), datetime.now(UTC) - timedelta(hours=1)
        with pytest.raises(TxIndexerQueryError):
            validate_timestamp_range(start, end)

    def test_window_accepts_datetimes(self):
        now = datetime.now(UTC)
        assert validate_timestamp_range(now, now) == (now, now)

    def test_strings_are_not_accepted_as_timestamps(self):
        with pytest.raises(TxIndexerQueryError):
            validate_timestamp_range("2026-01-01", None)

    def test_like_metacharacters_are_escaped(self):
        assert escape_like("100%_off\\") == "100\\%\\_off\\\\"


STELLAR_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _stellar_key() -> str:
    """A syntactically valid, unique Stellar public key for fixture isolation."""
    rng = random.Random(uuid4().int)
    return "G" + "".join(rng.choice(STELLAR_ALPHABET) for _ in range(55))


@pytest.fixture
def db_rows(db):
    """Three indexed rows with unique hashes/accounts/asset, removed afterwards.

    Filtering is scoped to a per-run asset code and account pair so rows left by
    other tests in the shared payment_transactions table cannot widen the result.
    """
    now = datetime.now(UTC)
    asset_code = f"T{uuid4().hex[:6].upper()}"
    sender, recipient = _stellar_key(), _stellar_key()
    rows = [
        IndexedTransaction(
            transaction_hash=uuid4().hex + uuid4().hex,
            type="payment",
            amount=amount,
            asset_code=asset_code,
            from_address=sender,
            to_address=recipient,
            created_at=now - timedelta(minutes=minutes),
        )
        for amount, minutes in ((100, 30), (2_000, 20), (3_000, 10))
    ]
    repo = TxIndexerRepository(db)
    repo.upsert_many(rows)
    yield repo, rows
    db.query(PaymentTransactionORM).filter(
        PaymentTransactionORM.transaction_hash.in_([row.transaction_hash for row in rows])
    ).delete(synchronize_session=False)
    db.commit()


@pytest.mark.postgres
class TestSearchFilters:
    def test_filters_combine_without_widening(self, db, db_rows):
        repo, rows = db_rows

        found = repo.search(asset_code=rows[0].asset_code.lower(), min_amount=3_000)

        assert [row.transaction_hash for row in found] == [rows[2].transaction_hash]

    def test_account_matches_either_party(self, db, db_rows):
        repo, rows = db_rows

        found = repo.search(account=rows[0].to_address, limit=200)

        assert {row.transaction_hash for row in found} == {row.transaction_hash for row in rows}

    def test_hash_filter_is_exact(self, db, db_rows):
        repo, rows = db_rows

        found = repo.search(transaction_hash=rows[1].transaction_hash)

        assert [row.transaction_hash for row in found] == [rows[1].transaction_hash]

    def test_sort_and_limit_are_applied(self, db, db_rows):
        repo, rows = db_rows

        found = repo.search(asset_code=rows[0].asset_code, sort_by="amount", sort_dir="desc", limit=2)

        assert [row.amount for row in found][:2] == [3_000, 2_000]

    def test_malicious_filter_is_rejected_before_any_sql(self, db, db_rows):
        repo, _ = db_rows

        with pytest.raises(TxIndexerQueryError):
            repo.search(sort_by="amount; DROP TABLE payment_transactions")
        with pytest.raises(TxIndexerQueryError):
            repo.search(transaction_hash=INJECTION)

        # The table is still there and still queryable.
        assert db.execute(text("SELECT count(*) FROM payment_transactions")).scalar() is not None

    def test_filters_are_bound_not_interpolated(self, db, db_rows):
        repo, rows = db_rows
        statements: list[tuple[str, dict]] = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append((statement, parameters))

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            repo.search(transaction_hash=rows[0].transaction_hash)
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        select_statement, params = next(
            (statement, parameters) for statement, parameters in statements if "payment_transactions" in statement
        )
        assert rows[0].transaction_hash not in select_statement
        assert any(value == rows[0].transaction_hash for value in params.values())


@pytest.fixture
def engineer_client():
    app.dependency_overrides[require_engineer] = lambda: None
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(require_engineer, None)


@pytest.mark.postgres
class TestSearchEndpoint:
    def test_rejects_injection_in_sort_key(self, engineer_client):
        # Short enough to pass the Query(max_length) guard, so the rejection is
        # the whitelist's and not Pydantic's generic validation error.
        response = engineer_client.get("/api/v1/indexer/transactions", params={"sort_by": "amount;DROP"})

        assert response.status_code == 422
        assert "sort_by" in response.json()["detail"]

    def test_rejects_injection_in_hash(self, engineer_client):
        response = engineer_client.get("/api/v1/indexer/transactions", params={"transaction_hash": INJECTION})

        assert response.status_code == 422
        assert "transaction_hash" in response.json()["detail"]

    def test_rejects_unknown_status(self, engineer_client):
        response = engineer_client.get("/api/v1/indexer/transactions", params={"status": "settled' OR '1'='1"})

        assert response.status_code == 422

    def test_table_survives_a_rejected_attempt(self, engineer_client, db):
        engineer_client.get("/api/v1/indexer/transactions", params={"transaction_hash": INJECTION})

        assert db.execute(text("SELECT count(*) FROM payment_transactions")).scalar() is not None

    def test_valid_query_returns_rows(self, engineer_client, db, db_rows):
        repo, rows = db_rows

        response = engineer_client.get(
            "/api/v1/indexer/transactions",
            params={"transaction_hash": rows[0].transaction_hash, "sort_by": "amount", "sort_dir": "asc"},
        )

        assert response.status_code == 200
        body = response.json()
        assert [item["transaction_hash"] for item in body] == [rows[0].transaction_hash]
        assert body[0]["amount"] == 100
