"""Add composite index on (outage_events.outage_id, occurred_at DESC)

Revision ID: 0021
Revises: 0020_audit_immutable
Create Date: 2026-07-29
"""
import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020_audit_immutable"
branch_labels = None
depends_on = None

### Changes
# **File: `/app/api/v1/endpoints/outages.py`**
# - `GET /{outage_id}/timeline`: Added `current_user=Depends(require_engineer)`
# - `POST /{outage_id}/recompute-sla`: Added `current_user=Depends(require_engineer)` + validation docstring

def upgrade():
    op.create_index(
        "ix_outage_events_outage_id_occurred_at",
        "outage_events",
        ["outage_id", sa.desc("occurred_at")],
        postgresql_using="btree",
        if_not_exists=True,
    )


def downgrade():
    op.drop_index(
        "ix_outage_events_outage_id_occurred_at",
        table_name="outage_events",
        if_exists=True,
    )
