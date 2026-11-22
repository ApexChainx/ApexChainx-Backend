"""MTTR (Mean Time To Repair) calculation, normalization, and formatting helpers.

This module provides small, dependency-light, pure utility functions used by
the SLA operations workspace for computing, normalizing, and formatting MTTR
values. The helpers are intentionally independent of application state, ORM
models, and frameworks so they can be reused by services, schemas, analytics,
and reporting code.

All functions are deterministic and handle invalid or edge-case inputs
explicitly, either by raising ``ValueError``/``TypeError`` or by returning a
safe default (typically ``None``).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Union

__all__ = [
    "compute_mttr_minutes",
    "compute_mttr_from_records",
    "normalize_mttr",
    "format_mttr",
    "SECONDS_PER_MINUTE",
    "MINUTES_PER_HOUR",
]

#: Number of seconds in one minute.
SECONDS_PER_MINUTE: float = 60.0

#: Number of minutes in one hour.
MINUTES_PER_HOUR: float = 60.0

#: Supported unit aliases mapped to canonical unit names.
_UNIT_ALIASES = {
    "s": "seconds",
    "sec": "seconds",
    "secs": "seconds",
    "second": "seconds",
    "seconds": "seconds",
    "m": "minutes",
    "min": "minutes",
    "mins": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
    "h": "hours",
    "hr": "hours",
    "hrs": "hours",
    "hour": "hours",
    "hours": "hours",
}

#: Conversion factors from each canonical unit to minutes.
_TO_MINUTES = {
    "seconds": 1.0 / SECONDS_PER_MINUTE,
    "minutes": 1.0,
    "hours": MINUTES_PER_HOUR,
}


def _ensure_aware_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    Naive datetimes are assumed to already be in UTC. Aware datetimes are
    converted to UTC.

    Args:
        value: A ``datetime`` instance, naive or aware.

    Returns:
        A timezone-aware ``datetime`` in UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_mttr_minutes(
    start: Optional[datetime],
    end: Optional[datetime],
    *,
    allow_negative: bool = False,
) -> Optional[float]:
    """Compute the MTTR duration in minutes between two timestamps.

    Naive datetimes are treated as UTC. Aware datetimes are converted to UTC
    before subtraction, so mixed naive/aware inputs are handled consistently.

    Args:
        start: The repair start timestamp (e.g. detected/acknowledged time).
        end: The repair end timestamp (e.g. resolved time).
        allow_negative: When ``False`` (default), a negative duration (i.e.
            ``end`` earlier than ``start``) raises ``ValueError``. When
            ``True``, the negative duration is returned as-is.

    Returns:
        The duration in minutes as a ``float``, or ``None`` if either
        timestamp is ``None``.

    Raises:
        TypeError: If ``start`` or ``end`` is not a ``datetime`` instance.
        ValueError: If the computed duration is negative and
            ``allow_negative`` is ``False``.
    """
    if start is None or end is None:
        return None
    if not isinstance(start, datetime):
        raise TypeError(
            f"start must be a datetime instance, got {type(start).__name__}"
        )
    if not isinstance(end, datetime):
        raise TypeError(
            f"end must be a datetime instance, got {type(end).__name__}"
        )

    start_utc = _ensure_aware_utc(start)
    end_utc = _ensure_aware_utc(end)

    delta = end_utc - start_utc
    minutes = delta.total_seconds() / SECONDS_PER_MINUTE

    if minutes < 0 and not allow_negative:
        raise ValueError(
            "end timestamp must not be earlier than start timestamp "
            f"(got {minutes} minutes)"
        )

    return minutes


def _extract_field(record: Any, *names: str) -> Any:
    """Extract the first present field from a mapping or object.

    Args:
        record: A mapping or object to read from.
        *names: Candidate field names, checked in order.

    Returns:
        The value of the first field found, or ``None`` if none are present.
    """
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                return record[name]
        return None

    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    return None


def compute_mttr_from_records(
    records: Optional[Iterable[Any]],
    *,
    allow_negative: bool = False,
    skip_invalid: bool = True,
) -> Optional[float]:
    """Compute the mean MTTR in minutes across a collection of records.

    Each record may be a mapping or an object. For every record the function
    looks for an explicit ``mttr_minutes`` value first; if absent, it derives
    the duration from ``detected_at``/``resolved_at`` (or the aliases
    ``start``/``end``) using :func:`compute_mttr_minutes`.

    Records that cannot produce a duration (missing fields, ``None`` values)
    are skipped. Records that raise an error during computation are skipped
    when ``skip_invalid`` is ``True`` (default) and otherwise propagate the
    error.

    Args:
        records: An iterable of mappings or objects. ``None`` is treated as an
            empty collection.
        allow_negative: Passed through to :func:`compute_mttr_minutes` for
            derived durations.
        skip_invalid: When ``True`` (default), records that fail to produce a
            valid duration are ignored. When ``False``, the first error is
            raised.

    Returns:
        The arithmetic mean of the collected durations in minutes, or ``None``
        if no valid durations were found.

    Raises:
        TypeError: If ``records`` is not iterable (and not ``None``).
        ValueError: If a record yields a negative duration and
            ``allow_negative`` is ``False`` and ``skip_invalid`` is ``False``.
    """
    if records is None:
        return None

    durations: list[float] = []

    for record in records:
        try:
            explicit = _extract_field(record, "mttr_minutes")
            if explicit is not None:
                value = float(explicit)
                if not math.isfinite(value):
                    raise ValueError("mttr_minutes must be finite")
                if value < 0 and not allow_negative:
                    raise ValueError("mttr_minutes must not be negative")
                durations.append(value)
                continue

            start = _extract_field(record, "detected_at", "start")
            end = _extract_field(record, "resolved_at", "end")
            minutes = compute_mttr_minutes(
                start, end, allow_negative=allow_negative
            )
            if minutes is not None:
                durations.append(minutes)
        except (TypeError, ValueError):
            if not skip_invalid:
                raise

    if not durations:
        return None

    return sum(durations) / len(durations)


def normalize_mttr(
    value: Optional[Union[int, float]],
    unit: str = "minutes",
) -> Optional[float]:
    """Normalize an MTTR value to minutes.

    Args:
        value: The numeric MTTR value. ``None`` returns ``None``.
        unit: The unit of ``value``. Accepts ``"seconds"``, ``"minutes"``,
            ``"hours"`` and common aliases (``"s"``, ``"min"``, ``"hr"``,
            etc.). Case-insensitive.

    Returns:
        The value converted to minutes as a ``float``, or ``None`` if ``value``
        is ``None``, non-finite (``NaN``/``inf``), or negative.

    Raises:
        TypeError: If ``value`` is not a real number (and not ``None``).
        ValueError: If ``unit`` is not a recognized unit.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"value must be a number or None, got {type(value).__name__}"
        )

    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return None

    if not isinstance(unit, str):
        raise TypeError(f"unit must be a string, got {type(unit).__name__}")

    canonical = _UNIT_ALIASES.get(unit.strip().lower())
    if canonical is None:
        raise ValueError(f"unsupported MTTR unit: {unit!r}")

    return numeric * _TO_MINUTES[canonical]


def format_mttr(
    minutes: Optional[Union[int, float]],
    precision: int = 2,
) -> str:
    """Format an MTTR value (in minutes) as a human-readable string.

    The output uses the most appropriate unit for readability:

    * less than one minute -> seconds (e.g. ``"45.00s"``)
    * less than one hour -> minutes (e.g. ``"12.50m"``)
    * otherwise -> hours (e.g. ``"2.50h"``)

    Args:
        minutes: The MTTR value in minutes. ``None``, non-finite, or negative
            values produce the safe default ``"N/A"``.
        precision: Number of decimal places to render. Negative values are
            clamped to ``0``.

    Returns:
        A formatted string, or ``"N/A"`` for invalid input.

    Raises:
        TypeError: If ``minutes`` is not a real number (and not ``None``) or
            ``precision`` is not an integer.
    """
    if minutes is None:
        return "N/A"
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        raise TypeError(
            f"minutes must be a number or None, got {type(minutes).__name__}"
        )
    if isinstance(precision, bool) or not isinstance(precision, int):
        raise TypeError(
            f"precision must be an integer, got {type(precision).__name__}"
        )

    numeric = float(minutes)
    if not math.isfinite(numeric) or numeric < 0:
        return "N/A"

    digits = max(precision, 0)

    if numeric < 1.0:
        seconds = numeric * SECONDS_PER_MINUTE
        return f"{seconds:.{digits}f}s"
    if numeric < MINUTES_PER_HOUR:
        return f"{numeric:.{digits}f}m"

    hours = numeric / MINUTES_PER_HOUR
    return f"{hours:.{digits}f}h"