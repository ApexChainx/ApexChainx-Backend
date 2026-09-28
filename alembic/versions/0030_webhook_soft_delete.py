# raw-sql-allowed
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


from alembic import op

revision = "0030_webhook_soft_delete"
down_revision = "0029_encrypt_webhook_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # if_not_exists / if_exists guards: this migration runs alongside the
    # 0029_sla_config_publish_state branch, and a partially-applied retry after
    # a failure in the sibling branch must not blow up on an existing column.
    op.execute("ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL")
    # Every existing row predates soft delete, so none of them is a tombstone.
    # The index keeps the "not deleted" filter on the list endpoint off a
    # sequential scan once the table has grown.
    op.execute("CREATE INDEX IF NOT EXISTS ix_webhooks_deleted_at ON webhooks (deleted_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_webhooks_deleted_at")
    op.execute("ALTER TABLE webhooks DROP COLUMN IF EXISTS deleted_at")
