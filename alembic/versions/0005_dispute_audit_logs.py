"""add dispute audit logs table

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dispute_audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("dispute_id", UUID(as_uuid=True), sa.ForeignKey("sla_disputes.id"), nullable=False, index=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("recorded_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dispute_audit_logs")
