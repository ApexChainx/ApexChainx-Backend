"""Persistence for :mod:`app.services.tx_indexer`.

The ingest path is append-mostly: every batch is a set of Horizon payments that
may already be stored (the cursor is at-least-once, and a retried batch
re-delivers rows). The insert is therefore a single parameterized PostgreSQL
``ON CONFLICT DO NOTHING`` statement keyed on the unique
``payment_transactions.transaction_hash`` — one round trip, no read-then-write
race, and a re-delivered batch inserts zero rows instead of raising.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.orm.payment import PaymentTransactionORM
from app.services.tx_indexer import IndexedTransaction


class TxIndexerRepository:
    """Idempotent sink for indexed chain transactions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_many(self, transactions: Sequence[IndexedTransaction]) -> int:
        """Insert *transactions*, skipping hashes that are already stored.

        Returns the number of rows actually inserted.
        """
        if not transactions:
            return 0

        rows = [
            {
                "id": tx.id,
                "transaction_hash": tx.transaction_hash,
                "type": tx.type,
                "amount": tx.amount,
                "asset_code": tx.asset_code,
                "from_address": tx.from_address,
                "to_address": tx.to_address,
                "status": "confirmed",
                "created_at": tx.created_at,
                "confirmed_at": datetime.now(UTC),
                "retry_count": 0,
            }
            for tx in transactions
        ]
        statement = (
            pg_insert(PaymentTransactionORM).values(rows).on_conflict_do_nothing(index_elements=["transaction_hash"])
        )
        result = self.db.execute(statement)
        self.db.commit()
        return int(result.rowcount or 0)
