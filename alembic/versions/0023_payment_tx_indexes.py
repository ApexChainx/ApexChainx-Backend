# raw-sql-allowed
"""Composite index on payment_transactions (status, created_at DESC).

Revision ID: 0023_payment_tx_indexes
Revises: 0020_audit_immutable
Create Date: 2026-07-29
"""
from alembic import op

revision = "0023_payment_tx_indexes"
down_revision = "0020_audit_immutable"
depends_on = None
branch_labels = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payment_tx_status_created
        ON payment_transactions (status, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_payment_tx_status_created")
