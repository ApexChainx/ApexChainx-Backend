"""Cursor-based pagination utilities.

Cursor pagination is O(1) per page and stable under concurrent writes,
unlike offset/limit which can skip or duplicate rows when new records
are inserted between page loads.

Usage::

    # Encoding a cursor from the last item of a page
    cursor = encode_cursor(last_item.id, last_item.created_at.isoformat())

    # Decoding a cursor from the request
    cursor_id, cursor_value = decode_cursor(request_cursor)

    # Using the cursor in a query (example with created_at)
    query = query.filter(
        or_(
            OutageORM.created_at < cursor_value,
            and_(
                OutageORM.created_at == cursor_value,
                OutageORM.id < cursor_id,
            ),
        )
    )
    query = query.order_by(OutageORM.created_at.desc(), OutageORM.id.desc()).limit(limit)
"""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel


def encode_cursor(id_value: Any, sort_value: str) -> str:
    """Encode an (id, sort_value) pair into a URL-safe opaque cursor string.

    Args:
        id_value: The unique identifier of the last item on the current page.
        sort_value: The sort-column value of the last item (ISO 8601 string or comparable).

    Returns:
        A base64url-encoded cursor string.
    """
    payload = json.dumps({"id": str(id_value), "v": sort_value}, sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[Any, str] | None:
    """Decode a cursor string back into (id, sort_value).

    Args:
        cursor: A base64url-encoded cursor string (or None).

    Returns:
        A tuple of (id, sort_value) or None if the cursor is invalid/missing.
    """
    if not cursor:
        return None
    try:
        # Pad back to a multiple of 4 for base64 decoding
        padded = cursor + "=" * (4 - len(cursor) % 4) if len(cursor) % 4 else cursor
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return payload["id"], payload["v"]
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return None


class CursorPage(BaseModel):
    """Standard cursor-paginated response envelope.

    Every list endpoint that supports cursor pagination returns this shape.

    .. code-block:: json

        {
          "items": [...],
          "next_cursor": "eyJpZCI6ICIuLi4iLCAidiI6ICIuLi4ifQ",
          "has_more": true
        }
    """

    items: list[Any]
    next_cursor: str | None = None
    has_more: bool
