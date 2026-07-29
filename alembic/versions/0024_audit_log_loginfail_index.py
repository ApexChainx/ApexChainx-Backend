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
