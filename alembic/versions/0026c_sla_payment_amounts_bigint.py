"""Convert sla_results.amount and payment_transactions.amount from float to bigint.

Revision ID: 0026c_sla_payment_amounts_bigint
Revises: 0026b_outage_updated_at_timestamptz
Create Date: 2026-08-26
"""
from alembic import op

revision = "0026c_sla_payment_amounts_bigint"
down_revision = "0026b_outage_updated_at_timestamptz"
depends_on = None
branch_labels = None


def upgrade() -> None:
    # Current values are integral (the on-chain i128 amounts), so rounding is
    # a no-op today and guards against any legacy fractional data.
    op.execute("ALTER TABLE sla_results ALTER COLUMN amount TYPE bigint USING ROUND(amount)::bigint")
    op.execute("ALTER TABLE payment_transactions ALTER COLUMN amount TYPE bigint USING ROUND(amount)::bigint")


def downgrade() -> None:
    op.execute("ALTER TABLE sla_results ALTER COLUMN amount TYPE double precision")
    op.execute("ALTER TABLE payment_transactions ALTER COLUMN amount TYPE double precision")
