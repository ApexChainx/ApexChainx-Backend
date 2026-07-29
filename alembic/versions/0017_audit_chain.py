"""BE-007: Add cryptographic chaining to audit log entries

Adds prev_hash and entry_hash columns to audit_logs so each entry is
cryptographically linked to its predecessor, providing tamper-evidence.

Revision ID: 0017_audit_chain
Revises: 0016_outage_event_schema_version
Create Date: 2026-07-28
"""
import hashlib
import json

import sqlalchemy as sa

from alembic import op

revision = "0017_audit_chain"
down_revision = "0016_outage_event_schema_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("entry_hash", sa.String(64), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, event_type, details, correlation_id, created_at "
            "FROM audit_logs ORDER BY id ASC"
        )
    ).fetchall()

    prev_hash = None
    for row in rows:
        data = {
            "prev_hash": prev_hash,
            "event_type": row.event_type,
            "details": row.details,
            "correlation_id": row.correlation_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        entry_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE audit_logs SET prev_hash = :prev, entry_hash = :hash WHERE id = :id"
            ),
            {"prev": prev_hash, "hash": entry_hash, "id": row.id},
        )
        prev_hash = entry_hash

    op.alter_column("audit_logs", "entry_hash", nullable=False)
    op.create_index("ix_audit_logs_entry_hash", "audit_logs", ["entry_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_audit_logs_entry_hash", table_name="audit_logs")
    op.drop_column("audit_logs", "entry_hash")
    op.drop_column("audit_logs", "prev_hash")
