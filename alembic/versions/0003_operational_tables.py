"""add operational tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


job_status_enum = sa.Enum(
    "pending",
    "started",
    "success",
    "failure",
    "revoked",
    name="jobstatus",
)
job_type_enum = sa.Enum(
    "sla_computation",
    "webhook_dispatch",
    "bulk_sla_computation",
    name="jobtype",
)
webhook_event_enum = sa.Enum(
    "sla.violation",
    "sla.warning",
    "sla.resolved",
    name="webhookevent",
)
webhook_delivery_status_enum = sa.Enum(
    "pending",
    "success",
    "failed",
    "retrying",
    name="webhookdeliverystatus",
)
dispute_status_enum = sa.Enum(
    "pending",
    "resolved",
    "rejected",
    name="disputestatus",
)


def upgrade() -> None:
    bind = op.get_bind()
    job_status_enum.create(bind, checkfirst=True)
    job_type_enum.create(bind, checkfirst=True)
    webhook_event_enum.create(bind, checkfirst=True)
    webhook_delivery_status_enum.create(bind, checkfirst=True)
    dispute_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=False),
        sa.Column("job_type", job_type_enum, nullable=False),
        sa.Column("status", job_status_enum, nullable=False, server_default="pending"),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("progress", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_celery_task_id", "jobs", ["celery_task_id"], unique=True)

    op.create_table(
        "webhooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("events", sa.Text(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("webhook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event", webhook_event_enum, nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "status",
            webhook_delivery_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sla_disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sla_result_id", sa.Integer(), nullable=False),
        sa.Column("flagged_by", sa.String(length=255), nullable=False),
        sa.Column("dispute_reason", sa.Text(), nullable=False),
        sa.Column("flagged_at", sa.DateTime(), nullable=False),
        sa.Column("status", dispute_status_enum, nullable=False, server_default="pending"),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["sla_result_id"], ["sla_results.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sla_disputes_sla_result_id", "sla_disputes", ["sla_result_id"])


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_sla_disputes_sla_result_id", table_name="sla_disputes")
    op.drop_table("sla_disputes")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_index("ix_jobs_celery_task_id", table_name="jobs")
    op.drop_table("jobs")

    dispute_status_enum.drop(bind, checkfirst=True)
    webhook_delivery_status_enum.drop(bind, checkfirst=True)
    webhook_event_enum.drop(bind, checkfirst=True)
    job_type_enum.drop(bind, checkfirst=True)
    job_status_enum.drop(bind, checkfirst=True)
