"""Pure datetime window utilities for the SLA operations workspace.

This module provides dependency-light helpers for parsing, bucketing, and
normalizing time windows. It is intentionally independent of application
state (no DB, no settings, no ORM) so it can be reused by services, schemas,
and analytics code within ``app.sla_operations``.

Conventions
-----------
* All datetimes are normalized to timezone-aware UTC values.
* Naive datetimes are interpreted as UTC.
* Windows are half-open: ``[start, end)`` — inclusive start, exclusive end.
* Buckets are ordered, contiguous, and non-overlapping.

Exported helpers
----------------
* :func:`parse_time_window`
* :func:`build_time_buckets`
* :func:`window_boundaries`
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Granularity",
    "TimeWindow",
    "TimeBucket",
    "GRANULARITIES",
    "parse_time_window",
    "build_time_buckets",
    "window_boundaries",
    "normalize_datetime",
    "ensure_utc",
]


# ---------------------------------------------------------------------------
# Types & constants
# ---------------------------------------------------------------------------

Granularity = str
"""Supported bucket granularity identifiers."""

GRANULARITIES: frozenset[str] = frozenset({"day", "week", "month"})
"""Set of granularities accepted by :func:`build_time_buckets`."""

_UTC = timezone.utc


@dataclass(frozen=True)
class TimeWindow:
    """A normalized, half-open time window ``[start, end)``.

    Attributes:
        start: Timezone-aware UTC start (inclusive).
        end: Timezone-aware UTC end (exclusive).
        granularity: Optional granularity hint (``"day"``, ``"week"``,
            ``"month"``) or ``None`` when unspecified.
    """

    start: datetime
    end: datetime
    granularity: Granularity | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("TimeWindow requires timezone-aware datetimes")
        if self.start >= self.end:
            raise ValueError(
                f"TimeWindow start must be strictly before end "
                f"(start={self.start.isoformat()}, end={self.end.isoformat()})"
            )
        if self.granularity is not None and self.granularity not in GRANULARITIES:
            raise ValueError(
                f"Unsupported granularity {self.granularity!r}; "
                f"expected one of {sorted(GRANULARITIES)}"
            )

    @property
    def duration(self) -> timedelta:
        """Return the window duration (``end - start``)."""
        return self.end - self.start

    def contains(self, moment: datetime) -> bool:
        """Return ``True`` if ``moment`` falls within ``[start, end)``.

        Naive ``moment`` values are treated as UTC.
        """
        normalized = normalize_datetime(moment)
        return self.start <= normalized < self.end


@dataclass(frozen=True)
class TimeBucket:
    """A single half-open bucket ``[start, end)`` with a label.

    Attributes:
        start: Timezone-aware UTC start (inclusive).
        end: Timezone-aware UTC end (exclusive).
        granularity: Granularity used to produce the bucket.
        label: Stable string label for the bucket (e.g. ``"2024-01-01"``).
    """

    start: datetime
    end: datetime
    granularity: Granularity
    label: str

    @property
    def duration(self) -> timedelta:
        """Return the bucket duration (``end - start``)."""
        return self.end - self.start


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_datetime(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    Naive datetimes are interpreted as UTC. Aware datetimes are converted to
    UTC.

    Args:
        value: A ``datetime`` instance.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Raises:
        TypeError: If ``value`` is not a ``datetime``.
    """
    if not isinstance(value, datetime):
        raise TypeError(
            f"Expected datetime, got {type(value).__name__}"
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=_UTC)
    return value.astimezone(_UTC)


# Alias for readability in call sites.
ensure_utc = normalize_datetime


def _coerce_datetime(value: Any, field: str) -> datetime:
    """Coerce ``value`` into a UTC datetime or raise ``ValueError``."""
    if isinstance(value, datetime):
        return normalize_datetime(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field} must not be an empty string")
        # Accept trailing 'Z' as UTC.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"{field} is not a valid ISO-8601 datetime: {value!r}"
            ) from exc
        return normalize_datetime(parsed)
    raise ValueError(
        f"{field} must be a datetime or ISO-8601 string, "
        f"got {type(value).__name__}"
    )


def _extract_field(source: Any, name: str) -> Any:
    """Extract ``name`` from a mapping or object, returning ``None`` if absent."""
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _coerce_granularity(value: Any) -> Granularity | None:
    """Validate and normalize a granularity value."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"granularity must be a string, got {type(value).__name__}"
        )
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in GRANULARITIES:
        raise ValueError(
            f"Unsupported granularity {value!r}; "
            f"expected one of {sorted(GRANULARITIES)}"
        )
    return normalized


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def parse_time_window(spec: Any) -> TimeWindow:
    """Parse a time window specification into a :class:`TimeWindow`.

    The ``spec`` may be a mapping (e.g. ``dict``) or an arbitrary object with
    ``start``, ``end``, and optional ``granularity`` attributes. ``start`` and
    ``end`` may be ``datetime`` instances or ISO-8601 strings. Naive datetimes
    are treated as UTC.

    Args:
        spec: Mapping or object exposing ``start``, ``end``, and optionally
            ``granularity``.

    Returns:
        A normalized :class:`TimeWindow` with UTC-aware bounds.

    Raises:
        TypeError: If ``spec`` is not a mapping or object with the required
            fields.
        ValueError: If ``start``/``end`` are missing, invalid, or if
            ``start >= end``. Also raised for unsupported granularity values.
    """
    if spec is None:
        raise TypeError("spec must not be None")

    raw_start = _extract_field(spec, "start")
    raw_end = _extract_field(spec, "end")
    raw_granularity = _extract_field(spec, "granularity")

    if raw_start is None:
        raise ValueError("spec is missing required field 'start'")
    if raw_end is None:
        raise ValueError("spec is missing required field 'end'")

    start = _coerce_datetime(raw_start, "start")
    end = _coerce_datetime(raw_end, "end")
    granularity = _coerce_granularity(raw_granularity)

    if start >= end:
        raise ValueError(
            f"start must be strictly before end "
            f"(start={start.isoformat()}, end={end.isoformat()})"
        )

    return TimeWindow(start=start, end=end, granularity=granularity)


def window_boundaries(
    start: datetime | str,
    end: datetime | str,
) -> tuple[datetime, datetime]:
    """Return normalized ``(start, end)`` UTC boundaries for a window.

    Both bounds are converted to timezone-aware UTC datetimes (naive inputs
    are treated as UTC). The window is half-open: ``[start, end)``.

    Args:
        start: Window start (``datetime`` or ISO-8601 string).
        end: Window end (``datetime`` or ISO-8601 string).

    Returns:
        A tuple ``(start_utc, end_utc)``.

    Raises:
        ValueError: If either bound is invalid, or if ``start >= end``
            (empty or inverted range).
    """
    start_utc = _coerce_datetime(start, "start")
    end_utc = _coerce_datetime(end, "end")
    if start_utc >= end_utc:
        raise ValueError(
            f"start must be strictly before end "
            f"(start={start_utc.isoformat()}, end={end_utc.isoformat()})"
        )
    return start_utc, end_utc


def _floor_to_granularity(moment: datetime, granularity: Granularity) -> datetime:
    """Floor ``moment`` (UTC) to the start of its granularity period."""
    if granularity == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "week":
        # ISO week starts on Monday.
        day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start - timedelta(days=day_start.weekday())
    if granularity == "month":
        return moment.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
    raise ValueError(f"Unsupported granularity {granularity!r}")


def _advance(moment: datetime, granularity: Granularity) -> datetime:
    """Return the start of the next granularity period after ``moment``."""
    if granularity == "day":
        return moment + timedelta(days=1)
    if granularity == "week":
        return moment + timedelta(days=7)
    if granularity == "month":
        year = moment.year + (1 if moment.month == 12 else 0)
        month = 1 if moment.month == 12 else moment.month + 1
        return moment.replace(year=year, month=month, day=1)
    raise ValueError(f"Unsupported granularity {granularity!r}")


def _bucket_label(moment: datetime, granularity: Granularity) -> str:
    """Return a stable label for the bucket starting at ``moment``."""
    if granularity == "day":
        return moment.strftime("%Y-%m-%d")
    if granularity == "week":
        iso_year, iso_week, _ = moment.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    if granularity == "month":
        return moment.strftime("%Y-%m")
    raise ValueError(f"Unsupported granularity {granularity!r}")


def build_time_buckets(
    start: datetime | str,
    end: datetime | str,
    granularity: Granularity,
) -> list[TimeBucket]:
    """Build ordered, non-overlapping buckets covering ``[start, end)``.

    Buckets are aligned to calendar boundaries for the requested granularity
    (day starts at 00:00 UTC, week starts on Monday, month starts on the 1st).
    The first bucket starts at ``start`` floored to the granularity boundary,
    and the last bucket ends at ``end`` floored to the granularity boundary
    (or the next boundary if ``end`` is not aligned). Buckets are clipped so
    that they never extend beyond ``end``.

    Args:
        start: Window start (``datetime`` or ISO-8601 string).
        end: Window end (``datetime`` or ISO-8601 string).
        granularity: One of ``"day"``, ``"week"``, or ``"month"``.

    Returns:
        A list of :class:`TimeBucket` instances ordered by ``start``. The list
        is empty when ``start == end`` after normalization.

    Raises:
        ValueError: If ``start``/``end`` are invalid, if ``start > end``, or
            if ``granularity`` is unsupported.
    """
    normalized_granularity = _coerce_granularity(granularity)
    if normalized_granularity is None:
        raise ValueError("granularity is required")

    start_utc = _coerce_datetime(start, "start")
    end_utc = _coerce_datetime(end, "end")

    if start_utc > end_utc:
        raise ValueError(
            f"start must not be after end "
            f"(start={start_utc.isoformat()}, end={end_utc.isoformat()})"
        )
    if start_utc == end_utc:
        return []

    buckets: list[TimeBucket] = []
    cursor = _floor_to_granularity(start_utc, normalized_granularity)

    while cursor < end_utc:
        next_cursor = _advance(cursor, normalized_granularity)
        bucket_start = max(cursor, start_utc)
        bucket_end = min(next_cursor, end_utc)
        if bucket_start < bucket_end:
            buckets.append(
                TimeBucket(
                    start=bucket_start,
                    end=bucket_end,
                    granularity=normalized_granularity,
                    label=_bucket_label(cursor, normalized_granularity),
                )
            )
        cursor = next_cursor

    return buckets