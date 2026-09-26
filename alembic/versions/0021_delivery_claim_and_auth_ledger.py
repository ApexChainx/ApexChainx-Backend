"""Claim webhook deliveries and persist auth throttling attempts."""
from alembic import op
import sqlalchemy as sa


revision = "0021_delivery_claim_auth_ledger"
down_revision = "0020_audit_immutable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE webhookdeliverystatus ADD VALUE IF NOT EXISTS 'sending'"
    )
    op.create_table(
        "auth_attempt_ledger",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("attempt_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_hash", "attempt_hash", name="uq_auth_attempt_scope_hash"
        ),
    )
    op.create_index(
        "ix_auth_attempt_ledger_scope_hash",
        "auth_attempt_ledger",
        ["scope_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_attempt_ledger_scope_hash",
        table_name="auth_attempt_ledger",
    )
    op.drop_table("auth_attempt_ledger")