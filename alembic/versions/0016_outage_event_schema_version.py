"""BE-020: Add schema_version to outage_events for payload versioning

Revision ID: 0016_outage_event_schema_version
Revises: 0015_audit_correlation
Create Date: 2026-04-29

Adds schema_version column to outage_events so consumers can safely
deserialize event payloads across future schema changes.
"""
import sqlalchemy as sa

from alembic import op

revision = "0016_outage_event_schema_version"
down_revision = "0015_audit_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outage_events",
        sa.Column("schema_version", sa.String(10), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("outage_events", "schema_version")
