"""Datetime window utilities for the SLA operations workspace.

This module provides small, dependency-free helpers for working with
time windows used by SLA operations analytics:

* :func:`parse_window` - normalize a window specification (datetimes or
  ISO-8601 strings) into a pair of timezone-aware ``datetime`` objects.
* :func:`generate_buckets` - split a window into aligned time buckets of a
  given size, using inclusive-start / exclusive-end semantics.
* :func:`iter_buckets` - lazily iterate over the same buckets.

All functions are deterministic and raise :class:`ValueError` for invalid
input (naive datetimes, non-positive bucket sizes, inverted ranges, etc.).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, List, Sequence, Tuple, Union

__all__ = [
    "parse_window",
    "generate_buckets",
    "iter_buckets",
    "WindowSpec",
    "Bucket",
]

# A window specification may be a datetime, an ISO-8601 string, or a
# two-element sequence of either.
DatetimeLike = Union[datetime, str]
WindowSpec = Union[Tuple[DatetimeLike, DatetimeLike], Sequence[DatetimeLike]]
Bucket = Tuple[datetime, datetime]


def _coerce_datetime(value: DatetimeLike, *, field: str) -> datetime:
    """Coerce ``value`` into a timezone-aware ``datetime``.

    Naive datetimes are rejected to avoid ambiguous window boundaries.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError(f"{field} must be a non-empty ISO-8601 string")
        # ``fromisoformat`` accepts a trailing ``Z`` on Python 3.11+; normalize
        # for older interpreters as well.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"{field} is not a valid ISO-8601 datetime: {value!r}") from exc
    else:
        raise ValueError(f"{field} must be a datetime or ISO-8601 string, got {type(value)!r}")

    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{field} must be timezone-aware, got naive datetime {dt!r}")
    return dt


def parse_window(window: WindowSpec) -> Tuple[datetime, datetime]:
    """Parse a window specification into ``(start, end)`` datetimes.

    Parameters
    ----------
    window:
        Either a ``(start, end)`` pair of datetimes/ISO strings, or a
        two-element sequence of the same.

    Returns
    -------
    tuple[datetime, datetime]
        Timezone-aware ``(start, end)`` pair.

    Raises
    ------
    ValueError
        If the window does not contain exactly two elements, if either
        boundary is naive/invalid, or if ``end <= start``.
    """
    if isinstance(window, (str, bytes)) or not isinstance(window, Sequence):
        raise ValueError("window must be a two-element sequence of datetimes or ISO strings")
    if len(window) != 2:
        raise ValueError(f"window must contain exactly two elements, got {len(window)}")

    start = _coerce_datetime(window[0], field="window start")
    end = _coerce_datetime(window[1], field="window end")

    if end <= start:
        raise ValueError(f"window end ({end.isoformat()}) must be after start ({start.isoformat()})")

    return start, end


def _validate_bucket_size(bucket_size: timedelta) -> None:
    if not isinstance(bucket_size, timedelta):
        raise ValueError(f"bucket_size must be a timedelta, got {type(bucket_size)!r}")
    if bucket_size <= timedelta(0):
        raise ValueError(f"bucket_size must be positive, got {bucket_size!r}")


def iter_buckets(
    start: datetime,
    end: datetime,
    bucket_size: timedelta,
) -> Iterator[Bucket]:
    """Yield ``(bucket_start, bucket_end)`` pairs covering ``[start, end)``.

    Buckets are aligned to ``start`` and the final bucket is truncated at
    ``end`` so that the union of all buckets exactly equals ``[start, end)``.
    """
    _validate_bucket_size(bucket_size)
    if start.tzinfo is None or start.tzinfo.utcoffset(start) is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.tzinfo.utcoffset(end) is None:
        raise ValueError("end must be timezone-aware")
    if end <= start:
        raise ValueError(f"end ({end.isoformat()}) must be after start ({start.isoformat()})")

    cursor = start
    while cursor < end:
        next_cursor = cursor + bucket_size
        if next_cursor > end:
            next_cursor = end
        yield cursor, next_cursor
        cursor = next_cursor


def generate_buckets(
    window: WindowSpec,
    bucket_size: timedelta,
) -> List[Bucket]:
    """Return a list of aligned ``(bucket_start, bucket_end)`` pairs.

    The window is parsed via :func:`parse_window` and split using
    inclusive-start / exclusive-end semantics.
    """
    start, end = parse_window(window)
    return list(iter_buckets(start, end, bucket_size))