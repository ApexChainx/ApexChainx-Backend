from __future__ import annotations

from typing import Iterable, Optional


def percentage(part: Optional[float], whole: Optional[float]) -> float:
    """Return ``part`` as a percentage of ``whole``.

    The result is ``part / whole * 100``. Divide-by-zero and missing inputs
    are handled deterministically:

    * If ``whole`` is ``None`` or ``0`` (or otherwise falsy), ``0.0`` is
      returned instead of raising.
    * If ``part`` is ``None``, it is treated as ``0``.

    Args:
        part: The portion of the whole. ``None`` is treated as ``0``.
        whole: The total. ``None`` or ``0`` yields ``0.0``.

    Returns:
        The percentage as a ``float``. Never raises for zero/empty input.
    """
    if not whole:
        return 0.0
    if part is None:
        part = 0.0
    return (part / whole) * 100.0


def ratio(
    numerator: Optional[float],
    denominator: Optional[float],
) -> Optional[float]:
    """Return ``numerator / denominator`` or ``None`` when undefined.

    Divide-by-zero and missing inputs are handled deterministically:

    * If ``denominator`` is ``None`` or ``0`` (or otherwise falsy), ``None``
      is returned instead of raising.
    * If ``numerator`` is ``None``, it is treated as ``0``.

    Args:
        numerator: The dividend. ``None`` is treated as ``0``.
        denominator: The divisor. ``None`` or ``0`` yields ``None``.

    Returns:
        The ratio as a ``float``, or ``None`` when the ratio is undefined.
    """
    if not denominator:
        return None
    if numerator is None:
        numerator = 0.0
    return numerator / denominator


def average_ratio(values: Optional[Iterable[Optional[float]]]) -> Optional[float]:
    """Return the arithmetic mean of ``values`` or ``None`` when empty.

    Empty and missing inputs are handled deterministically:

    * If ``values`` is ``None`` or contains no items, ``None`` is returned.
    * ``None`` entries within ``values`` are skipped. If, after skipping,
      no numeric values remain, ``None`` is returned.

    Args:
        values: An iterable of numbers (or ``None`` entries to skip).

    Returns:
        The mean as a ``float``, or ``None`` when there is nothing to average.
    """
    if values is None:
        return None
    total = 0.0
    count = 0
    for value in values:
        if value is None:
            continue
        total += value
        count += 1
    if count == 0:
        return None
    return total / count


def safe_divide(
    numerator: Optional[float],
    denominator: Optional[float],
    default: Optional[float] = None,
) -> Optional[float]:
    """Return ``numerator / denominator`` or ``default`` when undefined.

    Unlike :func:`ratio`, this helper lets callers choose the fallback value
    used when the division is undefined.

    Edge behavior:

    * If ``denominator`` is ``None`` or ``0`` (or otherwise falsy),
      ``default`` is returned instead of raising.
    * If ``numerator`` is ``None``, it is treated as ``0``.

    Args:
        numerator: The dividend. ``None`` is treated as ``0``.
        denominator: The divisor. ``None`` or ``0`` yields ``default``.
        default: Value returned when the division is undefined. Defaults to
            ``None``.

    Returns:
        The quotient as a ``float``, or ``default`` when undefined.
    """
    if not denominator:
        return default
    if numerator is None:
        numerator = 0.0
    return numerator / denominator


def clamp_percentage(
    value: Optional[float],
    minimum: float = 0.0,
    maximum: float = 100.0,
) -> float:
    """Clamp ``value`` into the ``[minimum, maximum]`` range.

    Useful for compliance percentages that must stay within sane bounds.

    Edge behavior:

    * ``None`` is treated as ``minimum``.
    * If ``minimum`` is greater than ``maximum``, the bounds are swapped so
      the result is always deterministic.
    * Values below ``minimum`` return ``minimum``; values above ``maximum``
      return ``maximum``.

    Args:
        value: The percentage to clamp. ``None`` is treated as ``minimum``.
        minimum: Lower bound of the range. Defaults to ``0.0``.
        maximum: Upper bound of the range. Defaults to ``100.0``.

    Returns:
        The clamped value as a ``float``.
    """
    low, high = minimum, maximum
    if low > high:
        low, high = high, low
    if value is None:
        return low
    if value < low:
        return low
    if value > high:
        return high
    return value


def violation_rate(
    violations: Optional[int],
    total: Optional[int],
) -> float:
    """Return the violation rate as a percentage of ``total``.

    Computes ``violations / total * 100`` for compliance reporting.

    Edge behavior:

    * If ``total`` is ``None`` or ``<= 0``, ``0.0`` is returned.
    * If ``violations`` is ``None``, it is treated as ``0``.
    * Negative ``violations`` are treated as ``0`` so the rate never goes
      below zero.

    Args:
        violations: Number of violations. ``None`` or negative values are
            treated as ``0``.
        total: Total number of observations. ``None`` or ``<= 0`` yields
            ``0.0``.

    Returns:
        The violation rate as a ``float`` percentage.
    """
    if total is None or total <= 0:
        return 0.0
    if violations is None or violations < 0:
        violations = 0
    return (violations / total) * 100.0
</parameter>