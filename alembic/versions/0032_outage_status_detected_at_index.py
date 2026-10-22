# raw-sql-allowed
"""Composite index for the primary incident view on outages (#579).

The dominant operator query filters by ``status`` plus a ``detected_at``
window and orders newest-first (``OutageRepository.list`` /
``list_cursor`` with status + start_date/end_date). Until now only
single-column indexes existed (``ix_outages_status`` from 0001, plus the
pg_trgm search indexes from 0028), so Postgres had to combine a bitmap of
``ix_outages_status`` with a sort or scan of the time dimension. A
composite btree on (status, detected_at) serves the equality + range +
order-by shape in one index: the status prefix pins the partition, the
detected_at suffix satisfies both the window predicate and the DESC
ordering, so the plan avoids a explicit Sort step on the incident view.

The DESC declaration matches the dominant "newest first" access pattern;
Postgres can read a btree backwards for ASC, so ASC queries remain
index-ordered too.

Revision ID: 0032_outage_status_detected_at_index
Revises: 0031_merge_open_heads
Create Date: 2026-09-28
"""
from alembic import op

revision = "0032_outage_status_detected_at_index"
down_revision = "0031_merge_open_heads"
depends_on = None
branch_labels = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_outages_status_detected_at ON outages (status, detected_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_outages_status_detected_at")
