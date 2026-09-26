# raw-sql-allowed
"""Switch sla_results.outage_id FK from CASCADE to RESTRICT with an orphan check.

Revision ID: 0027_sla_results_restrict_cascade
Revises: 0025_merge_branches
Create Date: 2026-08-26
"""
from alembic import op

revision = "0027_sla_results_restrict_cascade"
down_revision = "0025_merge_branches"
depends_on = None
branch_labels = None


def _drop_outage_fk() -> None:
    """Drop any FK constraint on sla_results referencing outages."""
    op.execute(
        """
        DO $$
        DECLARE
            con_name text;
        BEGIN
            SELECT conname INTO con_name
            FROM pg_constraint
            WHERE conrelid = 'sla_results'::regclass
              AND contype = 'f'
              AND confrelid = 'outages'::regclass
            ORDER BY conname
            LIMIT 1;
            IF con_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE sla_results DROP CONSTRAINT %I', con_name);
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    # Orphan check: remove SLA results that reference outages that no longer
    # exist, so the FK constraint can be added without dangling references.
    op.execute(
        """
        DELETE FROM sla_results sr
        WHERE NOT EXISTS (SELECT 1 FROM outages o WHERE o.id = sr.outage_id)
        """
    )
    _drop_outage_fk()
    op.create_foreign_key(
        "sla_results_outage_id_fkey",
        "sla_results",
        "outages",
        ["outage_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    _drop_outage_fk()
    op.create_foreign_key(
        "sla_results_outage_id_fkey",
        "sla_results",
        "outages",
        ["outage_id"],
        ["id"],
        ondelete="CASCADE",
    )
