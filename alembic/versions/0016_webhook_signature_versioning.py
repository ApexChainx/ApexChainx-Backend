"""Add signature versioning to webhook deliveries (BE-087).

Adds explicit signature version metadata to webhook deliveries:
- signature_version: Tracks which signature algorithm was used (defaults to 1)

This enables safe evolution of signing algorithms without breaking existing consumers.

Revision ID: 0016_webhook_sig_versioning
Revises: 0015_audit_correlation
Create Date: 2026-04-29
"""
import sqlalchemy as sa

from alembic import op

revision = "0016_webhook_sig_versioning"
down_revision = "0015_audit_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add signature_version column to webhook_deliveries
    op.add_column(
        "webhook_deliveries",
        sa.Column("signature_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("webhook_deliveries", "signature_version")
