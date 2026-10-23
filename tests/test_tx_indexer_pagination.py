"""Cursor pagination tests for the transaction index — Issue #717.

Paging must be stable under concurrent writes, must not lie about whether more
rows exist, and must reject a cursor it cannot interpret instead of silently
restarting from the top. The tests cover the HTTP contract with a fake
repository and the keyset predicate against a real (in-memory SQLite) table.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models.orm  # noqa: F401  (registers every ORM model on Base.metadata)
from app.core.security import require_engineer
from app.db.session import get_db
from app.main import app
from app.models.orm.payment import PaymentTransactionORM
from app.repositories.tx_indexer_repository import TxIndexerRepository
from app.utils.cursor import encode_cursor

BASE_TIME = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _hash(char: str) -> str:
    return char * 64


def _row(char: str, created_at: datetime, amount: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"idx_{_hash(char)}",
        transaction_hash=_hash(char),
        type="payment",
        amount=amount,
        asset_code="USDC",
        from_address="GAAAA",
        to_address="GBBBB",
        status="confirmed",
        created_at=created_at,
        confirmed_at=created_at,
    )


# ── HTTP contract ───────────────────────────────────────────────────────


@pytest.fixture
def client():
    app.dependency_overrides[require_engineer] = lambda: SimpleNamespace(role="engineer", email="eng@example.com")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def repository(monkeypatch):
    """Queue the rows the endpoint's repository call should return."""
    state: dict[str, object] = {"rows": [], "calls": []}

    def fake_list(self, **kwargs):
        state["calls"].append(kwargs)
        return state["rows"]

    monkeypatch.setattr(TxIndexerRepository, "list_indexed", fake_list)
    return state


class TestPageEnvelope:
    def test_first_page_reports_the_next_cursor(self, client, repository):
        repository["rows"] = [
            _row("c", BASE_TIME + timedelta(hours=2)),
            _row("b", BASE_TIME + timedelta(hours=1)),
            _row("a", BASE_TIME),
        ]

        response = client.get("/api/v1/indexer/transactions", params={"limit": 2})

        assert response.status_code == 200
        body = response.json()
        assert [item["transaction_hash"] for item in body["items"]] == [_hash("c"), _hash("b")]
        assert body["has_more"] is True
        assert body["next_cursor"]

    def test_last_page_has_no_cursor(self, client, repository):
        repository["rows"] = [_row("a", BASE_TIME)]

        response = client.get("/api/v1/indexer/transactions", params={"limit": 10})

        assert response.status_code == 200
        body = response.json()
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    def test_empty_result_is_an_empty_page(self, client, repository):
        repository["rows"] = []

        response = client.get("/api/v1/indexer/transactions")

        assert response.status_code == 200
        assert response.json() == {"items": [], "next_cursor": None, "has_more": False}

    def test_fetches_one_extra_row_to_detect_more(self, client, repository):
        repository["rows"] = []

        client.get("/api/v1/indexer/transactions", params={"limit": 25})

        assert repository["calls"][0]["limit"] == 26


class TestCursorHandling:
    def test_cursor_is_decoded_into_a_keyset_position(self, client, repository):
        cursor = encode_cursor("idx_" + _hash("b"), (BASE_TIME + timedelta(hours=1)).isoformat())
        repository["rows"] = [_row("a", BASE_TIME)]

        response = client.get("/api/v1/indexer/transactions", params={"cursor": cursor, "limit": 5})

        assert response.status_code == 200
        forwarded = repository["calls"][0]
        assert forwarded["after"] == (BASE_TIME + timedelta(hours=1), "idx_" + _hash("b"))
        assert forwarded["offset"] == 0
        assert forwarded["limit"] == 6

    def test_cursor_takes_precedence_over_offset(self, client, repository):
        cursor = encode_cursor("idx_" + _hash("b"), BASE_TIME.isoformat())
        repository["rows"] = []

        client.get("/api/v1/indexer/transactions", params={"cursor": cursor, "offset": 40})

        assert repository["calls"][0]["offset"] == 0

    def test_offset_path_still_works_without_a_cursor(self, client, repository):
        repository["rows"] = []

        client.get("/api/v1/indexer/transactions", params={"offset": 40, "limit": 10})

        assert repository["calls"][0]["offset"] == 40
        assert repository["calls"][0]["after"] is None

    def test_unreadable_cursor_is_a_422(self, client, repository):
        response = client.get("/api/v1/indexer/transactions", params={"cursor": "not-a-cursor"})

        assert response.status_code == 422
        assert response.json()["detail"] == "Invalid cursor"
        assert repository["calls"] == []

    def test_cursor_with_a_non_timestamp_position_is_a_422(self, client, repository):
        cursor = encode_cursor("idx_" + _hash("b"), "yesterday")

        response = client.get("/api/v1/indexer/transactions", params={"cursor": cursor})

        assert response.status_code == 422
        assert repository["calls"] == []

    def test_second_page_cursor_can_be_passed_back(self, client, repository):
        repository["rows"] = [
            _row("c", BASE_TIME + timedelta(hours=2)),
            _row("b", BASE_TIME + timedelta(hours=1)),
        ]
        first = client.get("/api/v1/indexer/transactions", params={"limit": 1}).json()

        repository["rows"] = [_row("b", BASE_TIME + timedelta(hours=1))]
        second = client.get(
            "/api/v1/indexer/transactions",
            params={"limit": 1, "cursor": first["next_cursor"]},
        )

        assert second.status_code == 200
        assert repository["calls"][-1]["after"] == (BASE_TIME + timedelta(hours=2), f"idx_{_hash('c')}")
        assert second.json()["items"][0]["transaction_hash"] == _hash("b")


# ── Keyset predicate against real SQL ───────────────────────────────────


@pytest.fixture(scope="function")
def engine():
    test_engine = create_engine("sqlite:///:memory:", echo=False)
    PaymentTransactionORM.__table__.create(bind=test_engine)
    yield test_engine
    PaymentTransactionORM.__table__.drop(bind=test_engine)


@pytest.fixture(scope="function")
def db(engine) -> Session:
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def seeded(db) -> TxIndexerRepository:
    db.add_all(
        [
            PaymentTransactionORM(
                id=f"idx_{_hash('a')}",
                transaction_hash=_hash("a"),
                type="payment",
                amount=1,
                asset_code="USDC",
                from_address="GAAAA",
                to_address="GBBBB",
                status="confirmed",
                created_at=BASE_TIME,
            ),
            PaymentTransactionORM(
                id=f"idx_{_hash('b')}",
                transaction_hash=_hash("b"),
                type="payment",
                amount=1,
                asset_code="USDC",
                from_address="GAAAA",
                to_address="GBBBB",
                status="confirmed",
                created_at=BASE_TIME + timedelta(hours=1),
            ),
            PaymentTransactionORM(
                id=f"idx_{_hash('c')}",
                transaction_hash=_hash("c"),
                type="payment",
                amount=1,
                asset_code="USDC",
                from_address="GAAAA",
                to_address="GBBBB",
                status="confirmed",
                created_at=BASE_TIME + timedelta(hours=2),
            ),
        ]
    )
    db.commit()
    return TxIndexerRepository(db)


class TestKeysetQuery:
    def test_first_page_starts_at_the_newest_row(self, seeded):
        rows = seeded.list_indexed(limit=2)
        assert [row.transaction_hash for row in rows] == [_hash("c"), _hash("b")]

    def test_cursor_returns_rows_strictly_older_than_the_position(self, seeded):
        rows = seeded.list_indexed(limit=2, after=(BASE_TIME + timedelta(hours=1), f"idx_{_hash('b')}"))
        assert [row.transaction_hash for row in rows] == [_hash("a")]

    def test_rows_inserted_during_paging_do_not_shift_the_next_page(self, seeded):
        first_page = seeded.list_indexed(limit=2)
        last = first_page[-1]

        # A new transaction lands at the head of the index while the client
        # holds the cursor; the second page must not repeat or skip rows.
        seeded.db.add(
            PaymentTransactionORM(
                id=f"idx_{_hash('d')}",
                transaction_hash=_hash("d"),
                type="payment",
                amount=1,
                asset_code="USDC",
                from_address="GAAAA",
                to_address="GBBBB",
                status="confirmed",
                created_at=BASE_TIME + timedelta(hours=3),
            )
        )
        seeded.db.commit()

        second_page = seeded.list_indexed(limit=2, after=(last.created_at, last.id))
        assert [row.transaction_hash for row in second_page] == [_hash("a")]

    def test_equal_timestamps_are_ordered_by_id(self, seeded):
        same_time = BASE_TIME + timedelta(hours=5)
        for char in ("e", "f"):
            seeded.db.add(
                PaymentTransactionORM(
                    id=f"idx_{_hash(char)}",
                    transaction_hash=_hash(char),
                    type="payment",
                    amount=1,
                    asset_code="USDC",
                    from_address="GAAAA",
                    to_address="GBBBB",
                    status="confirmed",
                    created_at=same_time,
                )
            )
        seeded.db.commit()

        first_page = seeded.list_indexed(limit=2)
        assert [row.transaction_hash for row in first_page] == [_hash("f"), _hash("e")]

        last = first_page[-1]
        next_page = seeded.list_indexed(limit=2, after=(last.created_at, last.id))
        assert _hash("e") not in [row.transaction_hash for row in next_page]
