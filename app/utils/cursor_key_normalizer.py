"""Normalizes cursor pagination sort keys to a single comparable type.

Callers of app/utils/cursor.py pass a mix of timezone-aware datetimes,
naive datetimes, and ISO strings for the same sort key, which can raise
TypeError or silently drop rows when compared directly.
"""

from datetime import UTC, datetime
from typing import Union

CursorKey = Union[str, datetime]


def normalize_cursor_key(value: CursorKey) -> str:
    """Coerce any supported cursor key into a UTC ISO-8601 string."""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"Unsupported cursor key type: {type(value)!r}")
