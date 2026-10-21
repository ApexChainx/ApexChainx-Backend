"""Helpers for the ``Webhook.previous_secrets`` grace-period history (#502).

Each rotation appends ``{hashed_secret, created_at, expires_at}`` to the JSONB
``previous_secrets`` column so a consumer can keep verifying with the old secret
during the grace window.  Nothing used to remove entries whose ``expires_at``
had passed on the rotation path, so the array — and therefore every read of the
row — grew for the lifetime of the webhook.

This module holds the single definition of "still in grace" so the rotate
endpoint and the daily housekeeping task cannot drift apart.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def parse_expiry(entry: Any) -> datetime | None:
    """Return the ``expires_at`` of a history entry as an aware datetime.

    Returns ``None`` when the entry is malformed or the timestamp cannot be
    parsed, so callers can distinguish "expired" from "unknown".
    """
    if not isinstance(entry, dict):
        return None
    raw = entry.get("expires_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        logger.warning("Webhook secret history: unparseable expires_at %r", raw)
        return None
    if parsed.tzinfo is None:
        # Legacy rows were written without an offset; treat them as UTC.
        return parsed.replace(tzinfo=UTC)
    return parsed


def is_within_grace(entry: Any, now: datetime) -> bool:
    """Return True when ``entry`` must still be honoured for verification.

    An entry whose expiry cannot be determined is kept: dropping a secret that
    might still be inside its grace window would break in-flight consumers,
    which is a worse failure than a stale row.  The malformed entry is logged so
    it stays visible.
    """
    expires_at = parse_expiry(entry)
    if expires_at is None:
        return True
    return expires_at > now


def prune_expired_secrets(entries: Any, now: datetime) -> tuple[list[dict[str, Any]], int]:
    """Drop out-of-grace entries, returning ``(kept, dropped_count)``."""
    if not entries:
        return [], 0
    if not isinstance(entries, list):
        logger.warning("Webhook secret history: expected a list, got %s", type(entries).__name__)
        return [], 0

    kept = [entry for entry in entries if is_within_grace(entry, now)]
    dropped = len(entries) - len(kept)
    return kept, dropped
