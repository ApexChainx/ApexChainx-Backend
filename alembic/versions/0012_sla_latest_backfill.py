"""Backfill SLA results to ensure only one latest per outage (BE-021).

This migration fixes any existing ambiguous rows where multiple SLA results
for the same outage have is_latest=True. It keeps only the most recent one
and demotes all others.

Revision ID: 0012_sla_latest_backfill
Revises: 0011_sla_latest_uniqueness
Create Date: 2026-04-28
"""
import sqlalchemy as sa

from alembic import op

revision = "0012_sla_latest_backfill"
down_revision = "0011_sla_latest_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This SQL finds duplicate is_latest=True rows per outage_id and keeps only
    # the one with the highest id (most recent), demoting all others to is_latest=False
    conn = op.get_bind()
    
    # First, identify outages with multiple is_latest=True rows
    duplicates_query = """
    SELECT outage_id, COUNT(*) as cnt
    FROM sla_results
    WHERE is_latest = true
    GROUP BY outage_id
    HAVING COUNT(*) > 1
    """
    
    result = conn.execute(sa.text(duplicates_query))
    duplicate_outages = result.fetchall()
    
    if duplicate_outages:
        print(f"Found {len(duplicate_outages)} outages with duplicate is_latest=True rows")
        
        # For each duplicate outage, keep only the row with the highest id
        for outage_id, count in duplicate_outages:
            print(f"  Fixing outage {outage_id}: {count} duplicate latest rows")
            
            # Demote all except the one with the highest id
            fix_query = """
            UPDATE sla_results
            SET is_latest = false
            WHERE outage_id = :outage_id
              AND is_latest = true
              AND id NOT IN (
                SELECT MAX(id)
                FROM sla_results
                WHERE outage_id = :outage_id
                  AND is_latest = true
              )
            """
            conn.execute(sa.text(fix_query), {"outage_id": outage_id})
        
        op.get_bind().commit()
        print("Backfill complete: all outages now have at most one is_latest=True row")
    else:
        print("No duplicate is_latest=True rows found. Backfill not needed.")


def downgrade() -> None:
    # No-op: we don't want to re-introduce ambiguity
    pass
