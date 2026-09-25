"""Retain webhook rows as tombstones on delete (issue #518).

``DELETE /api/v1/webhooks/{id}`` deleted the row, and the ``deliveries``
relationship cascades, so the delivery history operators rely on for audit was
destroyed with the registration. Adding a nullable ``deleted_at`` turns delete
into a soft delete: the row and its deliveries survive, the dispatcher stops
picking it up (``is_active`` is cleared at the same time), and the endpoint
layer hides it from the default listing.

Revision ID: 0030_webhook_soft_delete
Revises: 0029_encrypt_webhook_secrets
Create Date: 2026-09-01
"""

import sqlalchemy as sa

from alembic import op

revision = "0030_webhook_soft_delete"
down_revision = "0029_encrypt_webhook_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhooks",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        if_not_exists=True,
    )
    # Every existing row predates soft delete, so none of them is a tombstone.
    # The index keeps the "not deleted" filter on the list endpoint off a
    # sequential scan once the table has grown.
    op.create_index(
        "ix_webhooks_deleted_at",
        "webhooks",
        ["deleted_at"],
        postgresql_using="btree",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_webhooks_deleted_at", table_name="webhooks", if_exists=True)
    op.drop_column("webhooks", "deleted_at")
