# raw-sql-allowed
"""Partial index on audit_log for event_type='login_failure'.

Revision ID: 0024_audit_log_loginfail_index
Revises: 0020_audit_immutable
Create Date: 2026-07-29
"""
from alembic import op

revision = "0024_audit_log_loginfail_index"
down_revision = "0020_audit_immutable"
depends_on = None
branch_labels = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so run
    # it inside an autocommit block instead.
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_login_failure_created_at
            ON audit_logs (created_at DESC)
            WHERE event_type = 'login_failure'
            """
        )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_audit_logs_login_failure_created_at"
    )
