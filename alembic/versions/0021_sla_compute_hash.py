"""Add compute_hash column to sla_results for idempotent recompute (#35).

Adds a SHA-256 compute_hash column and a UNIQUE(outage_id, compute_hash)
constraint so retrying the same recompute returns the existing row instead
of creating a duplicate.

Revision ID: 0021_sla_compute_hash
Revises: 0020_audit_immutable
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0021_sla_compute_hash"
down_revision: Union[str, None] = "0020_audit_immutable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sla_results",
        sa.Column("compute_hash", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_sla_results_outage_compute_hash",
        "sla_results",
        ["outage_id", "compute_hash"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_sla_results_outage_compute_hash", "sla_results", type_="unique")
    op.drop_column("sla_results", "compute_hash")
