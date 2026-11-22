from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Union

__all__ = [
    "ComparisonDirection",
    "ThresholdComparisonResult",
    "compare_to_threshold",
    "is_breach",
]

Number = Union[int, float]


class ComparisonDirection(str, Enum):
    """Direction in which an observed metric is compared to a threshold.

    - ``AT_LEAST``: the metric must be greater than or equal to the threshold
      to be considered healthy (e.g. uptime percentage).
    - ``AT_MOST``: the metric must be less than or equal to the threshold to be
      considered healthy (e.g. error rate or latency).
    """

    AT_LEAST = "at_least"
    AT_MOST = "at_most"


class ThresholdStatus(str, Enum):
    """Relative position of the observed value compared to the threshold."""

    BELOW = "below"
    AT = "at"
    ABOVE = "above"


@dataclass(frozen=True)
class ThresholdComparisonResult:
    """Immutable result of comparing an observed value against a threshold.

    Attributes:
        observed: The observed metric value.
        threshold: The SLA threshold value.
        direction: The comparison direction used.
        meets_threshold: ``True`` when the observed value satisfies the SLA
            for the given direction. Exact equality always counts as meeting
            the threshold.
        delta: ``observed - threshold`` (positive when observed is above the
            threshold, negative when below, ``0`` when equal).
        status: Relative position of the observed value (``below``, ``at`` or
            ``above``).
    """

    observed: float
    threshold: float
    direction: ComparisonDirection
    meets_threshold: bool
    delta: float
    status: ThresholdStatus


def _validate_number(value: object, name: str) -> float:
    """Validate and normalize a numeric input to ``float``.

    Args:
        value: The value to validate.
        name: Human-readable parameter name used in error messages.

    Returns:
        The value as a ``float``.

    Raises:
        ValueError: If ``value`` is a ``bool``, not an ``int``/``float``, or is
            ``NaN`` or infinite.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not a bool: {value!r}")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be an int or float, got {type(value).__name__}")
    result = float(value)
    if math.isnan(result):
        raise ValueError(f"{name} must not be NaN")
    if math.isinf(result):
        raise ValueError(f"{name} must be finite, got {result!r}")
    return result


def _validate_direction(direction: object) -> ComparisonDirection:
    """Validate and normalize a comparison direction.

    Args:
        direction: A ``ComparisonDirection`` or its string value.

    Returns:
        The corresponding ``ComparisonDirection``.

    Raises:
        ValueError: If ``direction`` is not a recognized direction.
    """
    if isinstance(direction, ComparisonDirection):
        return direction
    try:
        return ComparisonDirection(direction)  # type: ignore[arg-type]
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"direction must be one of {[d.value for d in ComparisonDirection]}, "
            f"got {direction!r}"
        ) from exc


def compare_to_threshold(
    observed: Number,
    threshold: Number,
    direction: ComparisonDirection,
) -> ThresholdComparisonResult:
    """Compare an observed metric value against an SLA threshold.

    Args:
        observed: The observed metric value. Must be a finite ``int`` or
            ``float`` (bools are rejected).
        threshold: The SLA threshold value. Must be a finite ``int`` or
            ``float`` (bools are rejected).
        direction: The comparison direction, either a ``ComparisonDirection``
            or its string value (``"at_least"`` / ``"at_most"``).

    Returns:
        A ``ThresholdComparisonResult`` describing whether the observed value
        meets the threshold, the signed delta (``observed - threshold``) and
        the relative status.

    Edge behavior:
        - Exact equality always counts as meeting the threshold for both
          directions.
        - ``AT_LEAST`` meets the threshold when ``observed >= threshold``.
        - ``AT_MOST`` meets the threshold when ``observed <= threshold``.

    Raises:
        ValueError: If ``observed`` or ``threshold`` is a bool, non-numeric,
            ``NaN`` or infinite, or if ``direction`` is not recognized.
    """
    observed_value = _validate_number(observed, "observed")
    threshold_value = _validate_number(threshold, "threshold")
    normalized_direction = _validate_direction(direction)

    delta = observed_value - threshold_value

    if delta > 0:
        status = ThresholdStatus.ABOVE
    elif delta < 0:
        status = ThresholdStatus.BELOW
    else:
        status = ThresholdStatus.AT

    if normalized_direction is ComparisonDirection.AT_LEAST:
        meets_threshold = observed_value >= threshold_value
    else:
        meets_threshold = observed_value <= threshold_value

    return ThresholdComparisonResult(
        observed=observed_value,
        threshold=threshold_value,
        direction=normalized_direction,
        meets_threshold=meets_threshold,
        delta=delta,
        status=status,
    )


def is_breach(
    observed: Number,
    threshold: Number,
    direction: ComparisonDirection,
) -> bool:
    """Determine whether an observed value breaches an SLA threshold.

    A breach is the logical inverse of meeting the threshold: exact equality
    counts as meeting the threshold, so it is never a breach.

    Args:
        observed: The observed metric value. Must be a finite ``int`` or
            ``float`` (bools are rejected).
        threshold: The SLA threshold value. Must be a finite ``int`` or
            ``float`` (bools are rejected).
        direction: The comparison direction, either a ``ComparisonDirection``
            or its string value (``"at_least"`` / ``"at_most"``).

    Returns:
        ``True`` if the observed value violates the SLA for the given
        direction, ``False`` otherwise.

    Raises:
        ValueError: If ``observed`` or ``threshold`` is a bool, non-numeric,
            ``NaN`` or infinite, or if ``direction`` is not recognized.
    """
    return not compare_to_threshold(observed, threshold, direction).meets_threshold