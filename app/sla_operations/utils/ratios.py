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
</parameter>