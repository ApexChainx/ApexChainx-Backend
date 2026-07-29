# raw-sql-allowed
"""Make audit_logs table append-only via PG trigger (BE-013).

Adds a BEFORE DELETE trigger that prevents row deletion from the
audit_logs table, enforcing immutability of audit records.

Revision ID: 0020_audit_immutable
Revises: 0016_webhook_signature_versioning
Depends on: 0016_outage_event_schema_version
Create Date: 2026-07-28
"""
from alembic import op

revision = "0020_audit_immutable"
down_revision = "0016_webhook_signature_versioning"
depends_on = ("0016_outage_event_schema_version",)
branch_labels = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION block_audit_log_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_append_only
        BEFORE DELETE ON audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION block_audit_log_delete();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS block_audit_log_delete();")
