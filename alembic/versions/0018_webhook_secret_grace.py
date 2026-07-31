"""Add webhook secret grace period overlap window (BE-009).

Adds tracking for previous secrets with expiry:
- previous_secrets: JSONB list of hashed previous secrets with expiry timestamps
- secret_grace_hours: Per-webhook grace period configuration

Revision ID: 0018_webhook_secret_grace
Revises: 0016_webhook_signature_versioning
Create Date: 2026-07-28
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0018_webhook_secret_grace"
down_revision = "0016_webhook_signature_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhooks",
        sa.Column("previous_secrets", JSONB, default=list, nullable=False),
    )
    op.add_column(
        "webhooks",
        sa.Column("secret_grace_hours", sa.Integer(), default=24, nullable=False),
    )


def downgrade() -> None:
    op.drop_column("webhooks", "secret_grace_hours")
    op.drop_column("webhooks", "previous_secrets")