# raw-sql-allowed
"""Install pg_trgm and add GIN indexes for outage search columns.

Adds trigram GIN indexes on outages (id, site_id, site_name) so the
leading-wildcard ILIKE search used by OutageRepository can use the index
instead of a full sequential scan. Requires PostgreSQL >= 9.1 (pg_trgm);
in managed Postgres the extension may need superuser privileges.

Revision ID: 0028_outage_search_trgm
Revises: 0027_sla_results_restrict_cascade
Create Date: 2026-08-26
"""
from alembic import op

revision = "0028_outage_search_trgm"
down_revision = "0027_sla_results_restrict_cascade"
depends_on = None
branch_labels = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outages_id_trgm ON outages USING gin (id gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outages_site_id_trgm ON outages USING gin (site_id gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outages_site_name_trgm ON outages USING gin (site_name gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_outages_site_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_outages_site_id_trgm")
    op.execute("DROP INDEX IF EXISTS ix_outages_id_trgm")
