"""add outage_events and payment retry columns

Revision ID: 0005b
Revises: 0005
Create Date: 2026-03-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005b"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outage_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("outage_id", sa.String(), sa.ForeignKey("outages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outage_events_outage_id", "outage_events", ["outage_id"])

    op.add_column("payment_transactions", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("payment_transactions", sa.Column("last_retried_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_transactions", "last_retried_at")
    op.drop_column("payment_transactions", "retry_count")
    op.drop_index("ix_outage_events_outage_id", table_name="outage_events")
    op.drop_table("outage_events")
