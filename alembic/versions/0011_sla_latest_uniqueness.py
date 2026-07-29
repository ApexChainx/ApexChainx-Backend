"""Add partial unique index for latest SLA result per outage (BE-021).

This migration enforces at the database level that there can be only ONE
authoritative latest SLA result per outage at any time using a partial unique index.

Revision ID: 0011_sla_latest_uniqueness
Revises: 0010_wallet_persistence
Create Date: 2026-04-28
"""
from alembic import op

revision = "0011_sla_latest_uniqueness"
down_revision = "0010_wallet_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create a partial unique index that enforces only ONE row per outage_id
    # can have is_latest=True at any time
    op.create_index(
        "uq_sla_results_outage_latest",
        "sla_results",
        ["outage_id"],
        unique=True,
        postgresql_where="is_latest = true",  # Partial index: only applies when is_latest=True
    )


def downgrade() -> None:
    op.drop_index("uq_sla_results_outage_latest", table_name="sla_results")
