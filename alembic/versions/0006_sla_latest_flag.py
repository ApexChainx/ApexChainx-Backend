"""add is_latest flag to sla_results

Revision ID: 0006
Revises: 0005b
Create Date: 2026-04-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sla_results",
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_sla_results_outage_latest", "sla_results", ["outage_id", "is_latest"])


def downgrade() -> None:
    op.drop_index("ix_sla_results_outage_latest", table_name="sla_results")
    op.drop_column("sla_results", "is_latest")
