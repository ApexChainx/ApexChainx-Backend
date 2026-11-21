"""Threshold comparison and delta classification helpers for the SLA operations workspace.

This module is intentionally self-contained and free of dependencies on the
core application so that it can be exercised in isolation by contributor
tests. All helpers are pure functions with deterministic behavior.
"""

from __future__ import annotations

import math
from typing import Union

Number = Union[int, float]

# Classification labels returned by the helpers.
ABOVE = "above"
BELOW = "below"
AT = "at"

WITHIN = "within"
OVER = "over"
UNDER = "under"


def _validate_number(value: Number, name: str) -> float:
    """Coerce ``value`` to ``float`` and reject non-finite / non-numeric input.

    Args:
        value: The value to validate.
        name: Human-readable name used in error messages.

    Returns:
        The value as a ``float``.

    Raises:
        TypeError: If ``value`` is not an int or float (bools are rejected).
        ValueError: If ``value`` is NaN or infinite.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    result = float(value)
    if math.isnan(result) or math.isinf(result):
        raise ValueError(f"{name} must be a finite number, got {result!r}")
    return result


def compare_to_threshold(value: Number, threshold: Number) -> str:
    """Compare ``value`` against ``threshold``.

    Args:
        value: The observed value.
        threshold: The reference threshold.

    Returns:
        One of ``'above'``, ``'below'`` or ``'at'``.

    Raises:
        TypeError: If either argument is not a number.
        ValueError: If either argument is NaN or infinite.
    """
    value_f = _validate_number(value, "value")
    threshold_f = _validate_number(threshold, "threshold")

    if value_f > threshold_f:
        return ABOVE
    if value_f < threshold_f:
        return BELOW
    return AT


def classify_delta(delta: Number, tolerance: float = 0.0) -> str:
    """Classify a signed ``delta`` relative to a non-negative ``tolerance``.

    A delta whose magnitude is less than or equal to ``tolerance`` is
    considered ``'within'`` tolerance. Otherwise the sign of the delta
    determines whether it is ``'over'`` (positive) or ``'under'`` (negative).

    Args:
        delta: The signed delta value.
        tolerance: Non-negative tolerance band. Defaults to ``0.0``.

    Returns:
        One of ``'within'``, ``'over'`` or ``'under'``.

    Raises:
        TypeError: If ``delta`` or ``tolerance`` is not a number.
        ValueError: If ``delta`` is NaN/infinite or ``tolerance`` is negative,
            NaN or infinite.
    """
    delta_f = _validate_number(delta, "delta")
    tolerance_f = _validate_number(tolerance, "tolerance")

    if tolerance_f < 0:
        raise ValueError(f"tolerance must be non-negative, got {tolerance_f!r}")

    if abs(delta_f) <= tolerance_f:
        return WITHIN
    if delta_f > 0:
        return OVER
    return UNDER