"""Add webhook secret lifecycle metadata columns (BE-034).

Adds tracking for secret rotation events including:
- last_secret_rotation_at: timestamp of the most recent rotation
- secret_version: incremented counter for each rotation

Revision ID: 0013_webhook_secret_metadata
Revises: 0012_sla_latest_backfill
Create Date: 2026-04-28
"""
import sqlalchemy as sa

from alembic import op

revision = "0013_webhook_secret_metadata"
down_revision = "0012_sla_latest_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhooks",
        sa.Column("last_secret_rotation_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "webhooks",
        sa.Column("secret_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("webhooks", "secret_version")
    op.drop_column("webhooks", "last_secret_rotation_at")
