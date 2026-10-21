"""Cursor pagination for the block poller (issue #733).

The poller reads the payments that are still waiting for their confirmation.
Walking that backlog with OFFSET/LIMIT is wrong here: a payment that is
confirmed while the poller is walking shifts every later row by one, so the
poller either looks at a payment twice or skips one entirely. The rows that
have not been settled yet are exactly the rows that change under it.

The poller therefore pages with a cursor built from ``(created_at, id)`` --
the same key the payment list endpoint uses -- so a page is stable while the
backlog moves underneath it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.utils.cursor import decode_cursor, encode_cursor

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


@dataclass(frozen=True)
class PollerPage:
    """One page of pending payments plus the cursor for the next one."""

    payments: list[Any]
    next_cursor: str | None = None
    has_more: bool = False


def clamp_page_size(
    value: int,
    *,
    maximum: int = MAX_PAGE_SIZE,
    default: int = DEFAULT_PAGE_SIZE,
) -> int:
    """Clamp a requested page size into ``1..maximum``.

    Raises:
        ValueError: when the value is not an integer at all.
    """
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("page size must be an integer") from exc
    if size < 1:
        return default
    return min(size, maximum)


def encode_payment_cursor(payment_id: str, created_at: datetime) -> str:
    """Encode the key of the last payment of a page into a cursor."""
    return encode_cursor(payment_id, created_at.isoformat())


def decode_payment_cursor(cursor: str | None) -> tuple[str, datetime] | None:
    """Decode a cursor back into the ``(payment_id, created_at)`` key.

    A cursor that cannot be decoded is treated as "no cursor": the poller
    starts from the oldest pending payment instead of failing the cycle.
    """
    decoded = decode_cursor(cursor)
    if decoded is None:
        return None
    payment_id, sort_value = decoded
    try:
        created_at = datetime.fromisoformat(str(sort_value))
    except (TypeError, ValueError):
        return None
    return str(payment_id), created_at
