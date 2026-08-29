"""Persist SLA policy versions and publish tokens.

The sla_config_history table (0022) already enforces a unique
(severity, policy_version) pair via ix_sla_config_history_severity_version.
This migration only adds the publish_token column so optimistic-concurrency
tokens survive process restarts and are shared across workers (#272).

Revision ID: 0029_sla_config_publish_state
Revises: 0028_outage_search_trgm
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_sla_config_publish_state"
down_revision: Union[str, None] = "0028_outage_search_trgm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sla_config_history", sa.Column("publish_token", sa.String(64), nullable=True))
    op.execute("UPDATE sla_config_history SET publish_token = '' WHERE publish_token IS NULL")
    op.alter_column("sla_config_history", "publish_token", nullable=False)


def downgrade() -> None:
    op.drop_column("sla_config_history", "publish_token")
