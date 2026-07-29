"""Create sla_config_history table for atomic policy version publishes (#37).

Tracks every config publish with policy_version, content_hash, and
published_by so concurrent updates can be detected and history is auditable.

Revision ID: 0022_sla_config_history
Revises: 0021_sla_compute_hash
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022_sla_config_history"
down_revision: Union[str, None] = "0021_sla_compute_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sla_config_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("threshold_minutes", sa.Integer(), nullable=False),
        sa.Column("penalty_per_minute", sa.Integer(), nullable=False),
        sa.Column("reward_base", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sla_config_history_severity_version",
        "sla_config_history",
        ["severity", "policy_version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_sla_config_history_severity_version", table_name="sla_config_history")
    op.drop_table("sla_config_history")
