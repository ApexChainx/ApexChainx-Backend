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

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.orm.payment import PaymentTransactionORM
from app.services.tx_indexer import IndexedTransaction
from app.services.tx_indexer_query import (
    validate_account,
    validate_amount_range,
    validate_asset_code,
    validate_limit,
    validate_outage_id,
    validate_sort,
    validate_status,
    validate_timestamp_range,
    validate_transaction_hash,
    validate_transaction_type,
)


class TxIndexerRepository:
    """Idempotent sink for indexed chain transactions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    SORT_COLUMNS = {
        "created_at": PaymentTransactionORM.created_at,
        "confirmed_at": PaymentTransactionORM.confirmed_at,
        "amount": PaymentTransactionORM.amount,
        "status": PaymentTransactionORM.status,
    }

    def search(
        self,
        *,
        transaction_hash: object | None = None,
        account: object | None = None,
        asset_code: object | None = None,
        status: object | None = None,
        transaction_type: object | None = None,
        outage_id: object | None = None,
        min_amount: object | None = None,
        max_amount: object | None = None,
        created_after: object | None = None,
        created_before: object | None = None,
        sort_by: object | None = None,
        sort_dir: object | None = None,
        limit: object | None = None,
    ) -> list[PaymentTransactionORM]:
        """Return indexed transactions matching the validated filters.

        Every value is validated by ``app.services.tx_indexer_query`` *before*
        the query is built, then bound as a statement parameter. The ORDER BY
        column is looked up in ``SORT_COLUMNS`` by whitelisted name, so no user
        text ever becomes SQL.
        """
        hash_filter = validate_transaction_hash(transaction_hash)
        account_filter = validate_account(account)
        asset_filter = validate_asset_code(asset_code)
        status_filter = validate_status(status)
        type_filter = validate_transaction_type(transaction_type)
        outage_filter = validate_outage_id(outage_id)
        amount_low, amount_high = validate_amount_range(min_amount, max_amount)
        created_start, created_end = validate_timestamp_range(created_after, created_before)
        sort_field, direction = validate_sort(sort_by, sort_dir)
        row_limit = validate_limit(limit)

        query = self.db.query(PaymentTransactionORM)
        if hash_filter is not None:
            query = query.filter(PaymentTransactionORM.transaction_hash == hash_filter)
        if account_filter is not None:
            query = query.filter(
                or_(
                    PaymentTransactionORM.from_address == account_filter,
                    PaymentTransactionORM.to_address == account_filter,
                )
            )
        if asset_filter is not None:
            query = query.filter(PaymentTransactionORM.asset_code == asset_filter)
        if status_filter is not None:
            query = query.filter(PaymentTransactionORM.status == status_filter)
        if type_filter is not None:
            query = query.filter(PaymentTransactionORM.type == type_filter)
        if outage_filter is not None:
            query = query.filter(PaymentTransactionORM.outage_id == outage_filter)
        if amount_low is not None:
            query = query.filter(PaymentTransactionORM.amount >= amount_low)
        if amount_high is not None:
            query = query.filter(PaymentTransactionORM.amount <= amount_high)
        if created_start is not None:
            query = query.filter(PaymentTransactionORM.created_at >= created_start)
        if created_end is not None:
            query = query.filter(PaymentTransactionORM.created_at <= created_end)

        column = self.SORT_COLUMNS[sort_field]
        order = column.asc() if direction == "asc" else column.desc()
        return query.order_by(order, PaymentTransactionORM.id.asc()).limit(row_limit).all()

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
