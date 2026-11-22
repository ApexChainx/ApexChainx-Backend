"""MTTR (Mean Time To Repair) utility helpers.

This module is part of the isolated ``app.sla_operations`` contributor
workspace. It provides small, pure helpers for computing and formatting
Mean Time To Repair values. All functions are side-effect free and do not
depend on core application state.
"""

from __future__ import annotations

from typing import Iterable, Optional

__all__ = [
    "mean_time_to_repair",
    "format_duration",
]


def _validate_durations(durations: Iterable[float]) -> list[float]:
    """Materialize and validate an iterable of durations.

    Durations are expected to be expressed in seconds and must be
    non-negative. Negative values raise ``ValueError``.

    Args:
        durations: An iterable of numeric duration values in seconds.

    Returns:
        A list of validated float durations.

    Raises:
        ValueError: If any duration is negative.
        TypeError: If a duration is not a real number.
    """
    validated: list[float] = []
    for index, duration in enumerate(durations):
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise TypeError(
                f"Duration at index {index} must be a number, "
                f"got {type(duration).__name__}"
            )
        value = float(duration)
        if value < 0:
            raise ValueError(
                f"Duration at index {index} must be non-negative, got {value}"
            )
        validated.append(value)
    return validated


def mean_time_to_repair(durations: Iterable[float]) -> Optional[float]:
    """Compute the Mean Time To Repair for a collection of durations.

    Args:
        durations: An iterable of repair durations in seconds. Values must
            be non-negative.

    Returns:
        The arithmetic mean of the durations as a float, or ``None`` when
        the input is empty.

    Raises:
        ValueError: If any duration is negative.
        TypeError: If any duration is not a real number.
    """
    values = _validate_durations(durations)
    if not values:
        return None
    return sum(values) / len(values)


def format_duration(seconds: float) -> str:
    """Format a duration in seconds into a human-readable string.

    The output uses the largest sensible unit and includes up to two
    decimal places, trimming trailing zeros. Examples::

        format_duration(0)      -> "0s"
        format_duration(45)     -> "45s"
        format_duration(90)     -> "1.5m"
        format_duration(3600)   -> "1h"
        format_duration(5400)   -> "1.5h"
        format_duration(90000)  -> "1.04d"

    Args:
        seconds: A non-negative duration in seconds.

    Returns:
        A human-readable representation of the duration.

    Raises:
        ValueError: If ``seconds`` is negative.
        TypeError: If ``seconds`` is not a real number.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise TypeError(
            f"seconds must be a number, got {type(seconds).__name__}"
        )
    value = float(seconds)
    if value < 0:
        raise ValueError(f"seconds must be non-negative, got {value}")

    if value == 0:
        return "0s"

    units = (
        ("d", 86400.0),
        ("h", 3600.0),
        ("m", 60.0),
        ("s", 1.0),
    )

    for suffix, unit_seconds in units:
        if value >= unit_seconds:
            amount = value / unit_seconds
            formatted = f"{amount:.2f}".rstrip("0").rstrip(".")
            return f"{formatted}{suffix}"

    # Fallback for very small positive values below one second.
    formatted = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{formatted}s"