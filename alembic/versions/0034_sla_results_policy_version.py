"""Add columns that exist in the ORM but not in the migrated schema.

These fields were introduced over several feature branches without matching
migrations, so inserts and selects against a migrated database fail with
UndefinedColumn.

Revision ID: 0034_sla_results_policy_version
Revises: 0033_block_poller_pending_hash_index
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_sla_results_policy_version"
down_revision = "0033_block_poller_pending_hash_index"
depends_on = None
branch_labels = None


def upgrade() -> None:
    op.add_column(
        "sla_results",
        sa.Column(
            "policy_version",
            sa.String(length=50),
            nullable=False,
            server_default="1.0",
        ),
    )
    op.add_column(
        "sla_results",
        sa.Column(
            "threshold_source",
            sa.String(length=50),
            nullable=False,
            server_default="config",
        ),
    )
    op.add_column("sla_results", sa.Column("reason_code", sa.String(length=50), nullable=True))
    op.add_column("sla_results", sa.Column("decision_trace", sa.Text(), nullable=True))
    op.add_column("sla_disputes", sa.Column("baseline_sla_result_id", sa.Integer(), nullable=True))
    op.add_column("sla_disputes", sa.Column("proposed_sla_result_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_sla_disputes_baseline_result",
        "sla_disputes",
        "sla_results",
        ["baseline_sla_result_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_sla_disputes_proposed_result",
        "sla_disputes",
        "sla_results",
        ["proposed_sla_result_id"],
        ["id"],
    )
    op.add_column("sla_analytics_snapshots", sa.Column("checksum", sa.String(length=64), nullable=False, server_default=""))
    for column in ("progress_details", "partial_results", "per_item_errors"):
        op.add_column("jobs", sa.Column(column, sa.JSON(), nullable=True))


def downgrade() -> None:
    for column in ("per_item_errors", "partial_results", "progress_details"):
        op.drop_column("jobs", column)
    op.drop_column("sla_analytics_snapshots", "checksum")
    op.drop_constraint("fk_sla_disputes_proposed_result", "sla_disputes", type_="foreignkey")
    op.drop_constraint("fk_sla_disputes_baseline_result", "sla_disputes", type_="foreignkey")
    op.drop_column("sla_disputes", "proposed_sla_result_id")
    op.drop_column("sla_disputes", "baseline_sla_result_id")
    op.drop_column("sla_results", "decision_trace")
    op.drop_column("sla_results", "reason_code")
    op.drop_column("sla_results", "threshold_source")
    op.drop_column("sla_results", "policy_version")
