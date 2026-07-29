"""add sla_analytics_snapshots table

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sla_analytics_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_key", sa.String(100), nullable=False),
        sa.Column("total_outages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_violations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_rewards", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_penalties", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("net_payout", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("avg_mttr", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sla_analytics_snapshots_snapshot_key", "sla_analytics_snapshots", ["snapshot_key"])


def downgrade() -> None:
    op.drop_index("ix_sla_analytics_snapshots_snapshot_key", table_name="sla_analytics_snapshots")
    op.drop_table("sla_analytics_snapshots")
