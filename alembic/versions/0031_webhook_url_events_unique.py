# raw-sql-allowed
"""Partial unique index on (url, events) for live webhooks (issue #562).

Without a DB constraint, two concurrent ``POST /webhooks`` calls with the same
url+events tuple could both succeed, resulting in duplicate fan-out: every
matching event would fire twice for the same consumer.

The index is *partial* (``WHERE deleted_at IS NULL``) so that:
- Soft-deleted tombstones (#518) never block re-registration of the same url.
- The per-row check is enforced atomically by PostgreSQL instead of a
  serialisable application-level query.

``events`` is a TEXT column (JSON-encoded array). PostgreSQL's btree operator
accepts TEXT equality, so a unique index on ``(url, events)`` compares the
serialised representations directly. ``create_webhook`` uses
``canonical_json([e.value for e in payload.events])`` for the column, so the
serialisation is deterministic (keys sorted, no extra whitespace) and the
constraint fires reliably for truly identical subscriptions.

Because nothing prevented duplicates before this revision, the upgrade first
soft-deletes every live duplicate but the oldest. This mirrors the #518
tombstone convention — the row and its delivery history survive while it stops
being dispatched — and is required for the index to build on an existing
database.

Revision ID: 0031_webhook_url_events_unique
Revises: 0030_webhook_soft_delete
Create Date: 2026-09-28
"""

from alembic import op

revision = "0031_webhook_url_events_unique"
down_revision = "0030_webhook_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Collapse pre-existing duplicates, otherwise the unique index cannot be
    #    created ("could not create unique index ... Key (url, events) is
    #    duplicated"). Keep the oldest live row per (url, events); soft-delete
    #    the rest so their delivery history is retained (#518).
    op.execute(
        """
        UPDATE webhooks
        SET deleted_at = CURRENT_TIMESTAMP,
            is_active = false
        WHERE deleted_at IS NULL
          AND id IN (
              SELECT id FROM (
                  SELECT id,
                         ROW_NUMBER() OVER (
                             PARTITION BY url, events
                             ORDER BY created_at, id
                         ) AS rn
                  FROM webhooks
                  WHERE deleted_at IS NULL
              ) ranked
              WHERE ranked.rn > 1
          )
        """
    )

    # 2. Partial unique index: only live (non-deleted) webhooks are constrained.
    #    Deleted tombstones must not block re-registration of the same
    #    url/events.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_webhooks_url_events_live
        ON webhooks (url, events)
        WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_webhooks_url_events_live")
