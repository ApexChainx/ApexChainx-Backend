# raw-sql-allowed
from alembic import op

revision = "0011_payment_deduplication"
down_revision = "0010_wallet_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_payment_outage_type",
        "payment_transactions",
        ["outage_id", "type"],
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_sla_result_id
        ON payment_transactions (sla_result_id)
        WHERE sla_result_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_payment_sla_result_id")
    op.drop_constraint(
        "uq_payment_outage_type",
        "payment_transactions",
        type_="unique",
    )
