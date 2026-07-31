"""initial tables

Revision ID: 0001
Revises:
Create Date: 2026-02-23

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("site_name", sa.String(255), nullable=False),
        sa.Column("site_id", sa.String(255), nullable=True),
        sa.Column("severity", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="open"),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("affected_services", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("affected_subscribers", sa.Integer(), nullable=True),
        sa.Column("assigned_to", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("location", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("sla_status", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("mttr_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outages_id", "outages", ["id"])
    op.create_index("ix_outages_status", "outages", ["status"])

    op.create_table(
        "sla_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("outage_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("mttr_minutes", sa.Integer(), nullable=False),
        sa.Column("threshold_minutes", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("payment_type", sa.String(20), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["outage_id"], ["outages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sla_results_outage_id", "sla_results", ["outage_id"])

    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("transaction_hash", sa.String(255), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("asset_code", sa.String(20), nullable=False),
        sa.Column("from_address", sa.String(255), nullable=False),
        sa.Column("to_address", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("outage_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["outage_id"], ["outages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_hash"),
    )
    op.create_index("ix_payment_transactions_id", "payment_transactions", ["id"])
    op.create_index("ix_payment_transactions_status", "payment_transactions", ["status"])
    op.create_index("ix_payment_transactions_outage_id", "payment_transactions", ["outage_id"])


def downgrade() -> None:
    op.drop_table("payment_transactions")
    op.drop_table("sla_results")
    op.drop_table("outages")
