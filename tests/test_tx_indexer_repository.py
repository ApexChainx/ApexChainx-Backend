"""Repository tests for the TxIndexer index — Issue #710.

These run against a real (in-memory SQLite) database instead of a mock, so the
conflict-tolerant insert and the read filters are executed as SQL: replaying a
page must insert zero rows, duplicate hashes inside one page must collapse, and
the filters must be applied by the database rather than in Python.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models.orm  # noqa: F401  (registers every ORM model on Base.metadata)
from app.models.orm.payment import PaymentTransactionORM
from app.repositories.tx_indexer_repository import TxIndexerRepository
from app.services.tx_indexer import IndexedTransaction

BASE_TIME = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _hash(char: str) -> str:
    return char * 64


def _transaction(
    char: str = "a",
    *,
    amount: int = 100_000_000,
    asset_code: str = "USDC",
    from_address: str = "GAAAA",
    to_address: str = "GBBBB",
    created_at: datetime = BASE_TIME,
) -> IndexedTransaction:
    return IndexedTransaction(
        id=f"idx_{_hash(char)}",
        transaction_hash=_hash(char),
        type="payment",
        amount=amount,
        asset_code=asset_code,
        from_address=from_address,
        to_address=to_address,
        created_at=created_at,
    )


@pytest.fixture(scope="function")
def engine():
    # Only the index table is needed. ``Base.metadata.create_all`` cannot run on
    # SQLite because other domains use PostgreSQL ARRAY columns, so create the
    # single table under test instead.
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


class TestUpsert:
    def test_inserts_a_new_batch(self, db):
        assert TxIndexerRepository(db).upsert_many([_transaction("a"), _transaction("b")]) == 2

    def test_replaying_a_batch_inserts_nothing(self, db):
        repo = TxIndexerRepository(db)
        repo.upsert_many([_transaction("a")])

        assert repo.upsert_many([_transaction("a")]) == 0

    def test_keeps_the_existing_row_when_a_hash_is_replayed(self, db):
        repo = TxIndexerRepository(db)
        repo.upsert_many([_transaction("a", amount=1)])

        repo.upsert_many([_transaction("a", amount=999)])

        stored = repo.get_by_hash(_hash("a"))
        assert stored is not None
        assert stored.amount == 1

    def test_collapses_duplicate_hashes_inside_one_batch(self, db):
        repo = TxIndexerRepository(db)

        assert repo.upsert_many([_transaction("a"), _transaction("a")]) == 1

    def test_empty_batch_is_a_no_op(self, db):
        assert TxIndexerRepository(db).upsert_many([]) == 0

    def test_stored_rows_are_marked_confirmed(self, db):
        repo = TxIndexerRepository(db)
        repo.upsert_many([_transaction("a")])

        stored = repo.get_by_hash(_hash("a"))
        assert stored is not None
        assert stored.status == "confirmed"
        assert stored.confirmed_at is not None


class TestReadQueries:
    @pytest.fixture
    def seeded(self, db):
        repo = TxIndexerRepository(db)
        repo.upsert_many(
            [
                _transaction("a", asset_code="USDC", created_at=BASE_TIME),
                _transaction(
                    "b",
                    asset_code="XLM",
                    from_address="GCCCC",
                    to_address="GDDDD",
                    created_at=BASE_TIME + timedelta(hours=1),
                ),
                _transaction("c", asset_code="USDC", created_at=BASE_TIME + timedelta(hours=2)),
            ]
        )
        return repo

    def test_returns_newest_first(self, seeded):
        hashes = [row.transaction_hash for row in seeded.list_indexed()]
        assert hashes == [_hash("c"), _hash("b"), _hash("a")]

    def test_filters_by_asset_code(self, seeded):
        rows = seeded.list_indexed(asset_code="XLM")
        assert [row.transaction_hash for row in rows] == [_hash("b")]

    def test_account_matches_either_side(self, seeded):
        sent = seeded.list_indexed(account="GCCCC")
        received = seeded.list_indexed(account="GDDDD")
        assert [row.transaction_hash for row in sent] == [_hash("b")]
        assert [row.transaction_hash for row in received] == [_hash("b")]

    def test_filters_by_exact_hash(self, seeded):
        rows = seeded.list_indexed(transaction_hash=_hash("a"))
        assert [row.transaction_hash for row in rows] == [_hash("a")]

    def test_filters_by_created_window(self, seeded):
        rows = seeded.list_indexed(created_after=BASE_TIME + timedelta(minutes=30))
        assert [row.transaction_hash for row in rows] == [_hash("c"), _hash("b")]

        rows = seeded.list_indexed(created_before=BASE_TIME + timedelta(minutes=30))
        assert [row.transaction_hash for row in rows] == [_hash("a")]

    def test_limit_and_offset_page_the_results(self, seeded):
        first_page = seeded.list_indexed(limit=2, offset=0)
        second_page = seeded.list_indexed(limit=2, offset=2)

        assert [row.transaction_hash for row in first_page] == [_hash("c"), _hash("b")]
        assert [row.transaction_hash for row in second_page] == [_hash("a")]

    def test_unknown_hash_returns_none(self, seeded):
        assert seeded.get_by_hash("f" * 64) is None
