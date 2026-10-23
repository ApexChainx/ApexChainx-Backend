"""link payments to sla results

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("sla_result_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_payment_transactions_sla_result_id",
        "payment_transactions",
        "sla_results",
        ["sla_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_payment_transactions_sla_result_id",
        "payment_transactions",
        ["sla_result_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_transactions_sla_result_id", table_name="payment_transactions")
    op.drop_constraint(
        "fk_payment_transactions_sla_result_id",
        "payment_transactions",
        type_="foreignkey",
    )
    op.drop_column("payment_transactions", "sla_result_id")
