# raw-sql-allowed
"""Partial index for BlockPoller payment reconciliation (#723).

The poller confirms pending payments with a single batched
``UPDATE payment_transactions ... WHERE transaction_hash IN (...) AND status
= 'pending'``. The unique index on ``transaction_hash`` can find the rows, but
a partial index restricted to pending rows is smaller and avoids carrying
already-confirmed history for this hot path.

This revision also merges the three open heads present on main
(``0031_webhook_url_events_unique``, ``0032_outage_status_detected_at_index``
and ``218a9af9d890``) so ``alembic upgrade head`` resolves to one tip.

Revision ID: 0033_block_poller_pending_hash_index
Revises: 0031_webhook_url_events_unique, 0032_outage_status_detected_at_index, 218a9af9d890
Create Date: 2026-10-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0033_block_poller_pending_hash_index"
down_revision: Union[str, Sequence[str], None] = (
    "0031_webhook_url_events_unique",
    "0032_outage_status_detected_at_index",
    "218a9af9d890",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payment_tx_pending_hash
            ON payment_transactions (transaction_hash)
            WHERE status = 'pending'
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_payment_tx_pending_hash")
