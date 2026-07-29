"""Add composite index on (outage_events.outage_id, occurred_at DESC)

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-29
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_outage_events_outage_id_occurred_at",
        "outage_events",
        ["outage_id", "occurred_at DESC"],
        postgresql_using="btree",
        if_not_exists=True,
    )


def downgrade():
    op.drop_index(
        "ix_outage_events_outage_id_occurred_at",
        table_name="outage_events",
        if_exists=True,
    )
