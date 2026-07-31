"""Add resolved IPs to webhooks for SSRF protection.

Revision ID: 0017_webhook_resolved_ips
Revises: 0016_webhook_signature_versioning
Create Date: 2026-07-28
"""
import sqlalchemy as sa

from alembic import op

revision = "0017_webhook_resolved_ips"
down_revision = "0016_webhook_signature_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhooks",
        sa.Column("resolved_ips", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhooks", "resolved_ips")
