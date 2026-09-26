"""Convert outages.updated_at from naive timestamp to timezone-aware timestamptz.

Revision ID: 0026b_outage_updated_at_timestamptz
Revises: 0025_merge_branches
Create Date: 2026-08-26
"""
from alembic import op

revision = "0026b_outage_updated_at_timestamptz"
down_revision = "0025_merge_branches"
depends_on = None
branch_labels = None


def upgrade() -> None:
    # Stored values were written with datetime.now(UTC), so interpret the naive
    # column as UTC when converting to timestamptz (no instant shift).
    op.execute(
        "ALTER TABLE outages ALTER COLUMN updated_at TYPE timestamptz "
        "USING updated_at AT TIME ZONE 'UTC'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE outages ALTER COLUMN updated_at TYPE timestamp "
        "USING updated_at AT TIME ZONE 'UTC'"
    )
