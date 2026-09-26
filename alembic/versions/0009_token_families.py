"""add token families and session family tracking

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create token_families table
    op.create_table(
        "token_families",
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("current_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compromised", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("family_id"),
        sa.ForeignKeyConstraint(["email"], ["users.email"]),
    )
    op.create_index("ix_token_families_email", "token_families", ["email"])
    op.create_index("ix_token_families_family_id", "token_families", ["family_id"])

    # Add family_id and sequence to sessions table
    op.add_column("sessions", sa.Column("family_id", sa.String(64), nullable=True))
    op.add_column("sessions", sa.Column("sequence", sa.Integer(), nullable=True, server_default="0"))
    op.create_index("ix_sessions_family_id", "sessions", ["family_id"])
    op.create_foreign_key(
        "fk_sessions_token_families",
        "sessions",
        "token_families",
        ["family_id"],
        ["family_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_sessions_token_families", "sessions", type_="foreignkey")
    op.drop_index("ix_sessions_family_id", table_name="sessions")
    op.drop_column("sessions", "sequence")
    op.drop_column("sessions", "family_id")
    op.drop_index("ix_token_families_family_id", table_name="token_families")
    op.drop_index("ix_token_families_email", table_name="token_families")
    op.drop_table("token_families")
