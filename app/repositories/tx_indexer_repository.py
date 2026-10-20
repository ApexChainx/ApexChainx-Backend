"""Persistence for :mod:`app.services.tx_indexer` (#710).

The ingest path is append-mostly: every batch is a set of Horizon payments that
may already be stored, because the cursor is at-least-once and a re-delivered
page re-sends rows. The insert is therefore a single ``ON CONFLICT DO NOTHING``
statement keyed on the unique ``payment_transactions.transaction_hash``: one
round trip, no read-then-write race, and a re-delivered page inserts zero rows
instead of raising.

PostgreSQL is the production dialect; SQLite is supported because the test
suite runs the same code paths there. Any other backend falls back to a
read-then-insert in the same transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.orm.payment import PaymentTransactionORM
from app.services.tx_indexer import IndexedTransaction


class TxIndexerRepository:
    """Idempotent sink and read model for indexed chain transactions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── write side ──────────────────────────────────────────────────────

    def upsert_many(self, transactions: Sequence[IndexedTransaction]) -> int:
        """Insert *transactions*, skipping hashes that are already stored.

        Returns the number of rows actually inserted.
        """
        rows = self._rows(transactions)
        if not rows:
            return 0

        inserted = self.db.execute(self._insert_statement(rows)).rowcount
        self.db.commit()
        return int(inserted or 0)

    def _rows(self, transactions: Sequence[IndexedTransaction]) -> list[dict[str, Any]]:
        """Build insert rows, deduplicated by transaction hash.

        ``ON CONFLICT`` cannot be combined with two identical keys in the same
        statement, and the ingest path already collapses operations per
        transaction, so the dedupe here is a guard rather than a hot path.
        """
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for transaction in transactions:
            if transaction.transaction_hash in seen:
                continue
            seen.add(transaction.transaction_hash)
            rows.append(
                {
                    "id": transaction.id,
                    "transaction_hash": transaction.transaction_hash,
                    "type": transaction.type,
                    "amount": transaction.amount,
                    "asset_code": transaction.asset_code,
                    "from_address": transaction.from_address,
                    "to_address": transaction.to_address,
                    "status": transaction.status,
                    "created_at": transaction.created_at,
                    "confirmed_at": datetime.now(UTC),
                    "retry_count": 0,
                }
            )
        return rows

    def _insert_statement(self, rows: list[dict[str, Any]]) -> Any:
        """Return a conflict-tolerant INSERT for the configured dialect."""
        dialect = self.db.get_bind().dialect.name
        if dialect == "postgresql":
            return (
                postgresql_insert(PaymentTransactionORM)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["transaction_hash"])
            )
        if dialect == "sqlite":
            return (
                sqlite_insert(PaymentTransactionORM)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["transaction_hash"])
            )
        return self._dedupe_against_existing(rows)

    def _dedupe_against_existing(self, rows: list[dict[str, Any]]) -> Any:
        """Portable fallback: drop hashes already in the table, then insert."""
        hashes = [row["transaction_hash"] for row in rows]
        existing = {
            value
            for (value,) in self.db.query(PaymentTransactionORM.transaction_hash)
            .filter(PaymentTransactionORM.transaction_hash.in_(hashes))
            .all()
        }
        fresh = [row for row in rows if row["transaction_hash"] not in existing]
        return PaymentTransactionORM.__table__.insert().values(fresh)

    # ── read side ───────────────────────────────────────────────────────

    def list_indexed(
        self,
        *,
        transaction_hash: str | None = None,
        account: str | None = None,
        asset_code: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PaymentTransactionORM]:
        """Return indexed transactions matching every supplied filter."""
        query = self.build_query(
            transaction_hash=transaction_hash,
            account=account,
            asset_code=asset_code,
            status=status,
            transaction_type=transaction_type,
            created_after=created_after,
            created_before=created_before,
        )
        return list(
            query.order_by(PaymentTransactionORM.created_at.desc(), PaymentTransactionORM.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_by_hash(self, transaction_hash: str) -> PaymentTransactionORM | None:
        """Return the indexed row for *transaction_hash*, if any."""
        return (
            self.db.query(PaymentTransactionORM)
            .filter(PaymentTransactionORM.transaction_hash == transaction_hash)
            .first()
        )

    def build_query(
        self,
        *,
        transaction_hash: str | None = None,
        account: str | None = None,
        asset_code: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> Any:
        """Build the filtered query shared by the list and count paths."""
        query = self.db.query(PaymentTransactionORM)

        if transaction_hash:
            query = query.filter(PaymentTransactionORM.transaction_hash == transaction_hash)
        if account:
            query = query.filter(
                (PaymentTransactionORM.from_address == account) | (PaymentTransactionORM.to_address == account)
            )
        if asset_code:
            query = query.filter(PaymentTransactionORM.asset_code == asset_code)
        if status:
            query = query.filter(PaymentTransactionORM.status == status)
        if transaction_type:
            query = query.filter(PaymentTransactionORM.type == transaction_type)
        if created_after:
            query = query.filter(PaymentTransactionORM.created_at >= created_after)
        if created_before:
            query = query.filter(PaymentTransactionORM.created_at <= created_before)
        return query
