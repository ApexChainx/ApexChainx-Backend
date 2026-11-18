"""Cascade token-family deletion to sessions.

Revision ID: 0035_cascade_token_family_sessions
Revises: 0034_sla_results_policy_version
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_cascade_token_family_sessions"
down_revision = "0034_sla_results_policy_version"
depends_on = None
branch_labels = None


def upgrade() -> None:
    op.drop_constraint("fk_sessions_token_families", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "fk_sessions_token_families",
        "sessions",
        "token_families",
        ["family_id"],
        ["family_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_sessions_token_families", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "fk_sessions_token_families",
        "sessions",
        "token_families",
        ["family_id"],
        ["family_id"],
    )
