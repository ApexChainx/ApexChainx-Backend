"""Add ORM-declared columns that no migration ever created.

The ORM gained columns over time without a matching migration, so any code
path touching them died with ``UndefinedColumn`` against a migrated database:

- sla_results: policy_version, threshold_source, reason_code, decision_trace
  (the SLA recomputation INSERT lists all four, so every dispute/SLA test
  errored at setup).
- sla_disputes: baseline_sla_result_id, proposed_sla_result_id (#268 dispute
  baseline/proposal links).
- jobs: progress_details, partial_results, per_item_errors (bulk-job tracking).
- webhook_deliveries: dead_lettered_at (BE-086 dead-letter timestamp).

NOT NULL columns are added with a temporary server_default to backfill existing
rows, then the default is dropped so the schema matches the ORM exactly (the
ORM supplies these values client-side).

Revision ID: 0032_orm_column_backfill
Revises: 0031_merge_heads
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_orm_column_backfill"
down_revision = "0031_merge_heads"
depends_on = None
branch_labels = None


def upgrade() -> None:
    op.add_column("sla_results", sa.Column("policy_version", sa.String(50), nullable=False, server_default="1.0"))
    op.add_column("sla_results", sa.Column("threshold_source", sa.String(50), nullable=False, server_default="config"))
    op.add_column("sla_results", sa.Column("reason_code", sa.String(50), nullable=True))
    op.add_column("sla_results", sa.Column("decision_trace", sa.Text(), nullable=True))
    # Match the ORM: values come from the application, not the server.
    op.alter_column("sla_results", "policy_version", server_default=None)
    op.alter_column("sla_results", "threshold_source", server_default=None)

    op.add_column("sla_disputes", sa.Column("baseline_sla_result_id", sa.Integer(), nullable=True))
    op.add_column("sla_disputes", sa.Column("proposed_sla_result_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "sla_disputes_baseline_sla_result_id_fkey",
        "sla_disputes",
        "sla_results",
        ["baseline_sla_result_id"],
        ["id"],
    )
    op.create_foreign_key(
        "sla_disputes_proposed_sla_result_id_fkey",
        "sla_disputes",
        "sla_results",
        ["proposed_sla_result_id"],
        ["id"],
    )

    op.add_column("jobs", sa.Column("progress_details", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("partial_results", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("per_item_errors", sa.JSON(), nullable=True))

    op.add_column("webhook_deliveries", sa.Column("dead_lettered_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("webhook_deliveries", "dead_lettered_at")

    op.drop_column("jobs", "per_item_errors")
    op.drop_column("jobs", "partial_results")
    op.drop_column("jobs", "progress_details")

    op.drop_constraint("sla_disputes_proposed_sla_result_id_fkey", "sla_disputes", type_="foreignkey")
    op.drop_constraint("sla_disputes_baseline_sla_result_id_fkey", "sla_disputes", type_="foreignkey")
    op.drop_column("sla_disputes", "proposed_sla_result_id")
    op.drop_column("sla_disputes", "baseline_sla_result_id")

    op.drop_column("sla_results", "decision_trace")
    op.drop_column("sla_results", "reason_code")
    op.drop_column("sla_results", "threshold_source")
    op.drop_column("sla_results", "policy_version")
