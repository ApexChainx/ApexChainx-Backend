"""Add wallet persistence table with uniqueness constraints (BE-032).

Revision ID: 0010_wallet_persistence
Revises: 0009_token_families
Create Date: 2026-04-28
"""
import sqlalchemy as sa

from alembic import op

revision = "0010_wallet_persistence"
down_revision = "0009_token_families"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=255), nullable=False, unique=True),
        sa.Column("public_key", sa.String(length=56), nullable=False, unique=True),
        sa.Column("funded", sa.Boolean(), nullable=False, default=False),
        sa.Column("active", sa.Boolean(), nullable=False, default=True),
        sa.Column("trustline_ready", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.Column("cached_at", sa.DateTime(), nullable=True),
        sa.Index("ix_wallets_user_id", "user_id", unique=True),
        sa.Index("ix_wallets_public_key", "public_key", unique=True),
    )


def downgrade() -> None:
    op.drop_table("wallets")
