"""Pure statistical helper utilities for the SLA operations workspace.

This module provides small, dependency-light, well-typed helpers for computing
basic descriptive statistics over numeric iterables. It is intended for reuse by
analytics, reporting, validation, and operational-insight modules within the
``app.sla_operations`` workspace package.

Design notes
------------
* All helpers are pure functions: they do not read or mutate global state.
* Inputs are materialized into a list of floats before computation, so any
  iterable (generators, tuples, sets, etc.) is accepted.
* ``bool`` values are explicitly rejected even though ``bool`` is a subclass of
  ``int`` in Python, because treating ``True``/``False`` as ``1``/``0`` is
  almost always a bug in analytics code.
* Non-numeric values, ``NaN``, and infinite values raise ``ValueError``.
* Empty inputs raise ``ValueError`` for every helper in this module.

Percentile method
-----------------
``percentile`` uses linear interpolation between closest ranks (the same method
as ``numpy.percentile`` with ``method="linear"`` and ``statistics.quantiles``
with ``method="inclusive"``). For a sorted sample of ``n`` values and a
percentile ``p`` in ``[0, 100]``, the rank is computed as::

    rank = (p / 100) * (n - 1)

The result is the linear interpolation between ``sorted_values[floor(rank)]``
and ``sorted_values[ceil(rank)]``. This yields ``p=0`` equal to the minimum and
``p=100`` equal to the maximum.
"""

from __future__ import annotations

import math
from typing import Iterable, List

__all__ = [
    "minimum",
    "maximum",
    "mean",
    "median",
    "percentile",
]


def _to_float_list(values: Iterable[float]) -> List[float]:
    """Validate and materialize an iterable of numbers into a list of floats.

    Parameters
    ----------
    values:
        An iterable of numeric values. ``bool`` values are rejected.

    Returns
    -------
    list[float]
        The validated values as floats, in their original order.

    Raises
    ------
    ValueError
        If ``values`` is empty, contains a ``bool``, contains a non-numeric
        value, or contains ``NaN`` or an infinite value.
    TypeError
        If ``values`` is not iterable.
    """
    if values is None:
        raise ValueError("values must be a non-empty iterable of numbers")

    try:
        iterator = iter(values)
    except TypeError as exc:  # pragma: no cover - defensive
        raise TypeError("values must be an iterable of numbers") from exc

    result: List[float] = []
    for item in iterator:
        if isinstance(item, bool):
            raise ValueError("boolean values are not allowed in numeric input")
        if not isinstance(item, (int, float)):
            raise ValueError(
                f"non-numeric value encountered: {item!r} (type {type(item).__name__})"
            )
        as_float = float(item)
        if math.isnan(as_float):
            raise ValueError("NaN values are not allowed in numeric input")
        if math.isinf(as_float):
            raise ValueError("infinite values are not allowed in numeric input")
        result.append(as_float)

    if not result:
        raise ValueError("values must be a non-empty iterable of numbers")

    return result


def minimum(values: Iterable[float]) -> float:
    """Return the smallest value in ``values``.

    Parameters
    ----------
    values:
        A non-empty iterable of numeric values.

    Returns
    -------
    float
        The minimum value.

    Raises
    ------
    ValueError
        If ``values`` is empty or contains invalid entries (bools, non-numeric,
        ``NaN``, or infinite values).
    """
    return min(_to_float_list(values))


def maximum(values: Iterable[float]) -> float:
    """Return the largest value in ``values``.

    Parameters
    ----------
    values:
        A non-empty iterable of numeric values.

    Returns
    -------
    float
        The maximum value.

    Raises
    ------
    ValueError
        If ``values`` is empty or contains invalid entries (bools, non-numeric,
        ``NaN``, or infinite values).
    """
    return max(_to_float_list(values))


def mean(values: Iterable[float]) -> float:
    """Return the arithmetic mean of ``values``.

    Parameters
    ----------
    values:
        A non-empty iterable of numeric values.

    Returns
    -------
    float
        The arithmetic mean.

    Raises
    ------
    ValueError
        If ``values`` is empty or contains invalid entries (bools, non-numeric,
        ``NaN``, or infinite values).
    """
    data = _to_float_list(values)
    return sum(data) / len(data)


def median(values: Iterable[float]) -> float:
    """Return the median of ``values``.

    For an even number of values, the median is the average of the two middle
    values after sorting.

    Parameters
    ----------
    values:
        A non-empty iterable of numeric values.

    Returns
    -------
    float
        The median value.

    Raises
    ------
    ValueError
        If ``values`` is empty or contains invalid entries (bools, non-numeric,
        ``NaN``, or infinite values).
    """
    data = sorted(_to_float_list(values))
    n = len(data)
    mid = n // 2
    if n % 2 == 1:
        return data[mid]
    return (data[mid - 1] + data[mid]) / 2.0


def percentile(values: Iterable[float], p: float) -> float:
    """Return the ``p``-th percentile of ``values`` using linear interpolation.

    The percentile is computed by sorting the input and interpolating linearly
    between the closest ranks (equivalent to ``numpy.percentile`` with
    ``method="linear"``). ``p=0`` returns the minimum and ``p=100`` returns the
    maximum.

    Parameters
    ----------
    values:
        A non-empty iterable of numeric values.
    p:
        The percentile to compute, as a number in the inclusive range
        ``[0, 100]``.

    Returns
    -------
    float
        The interpolated percentile value.

    Raises
    ------
    ValueError
        If ``p`` is a ``bool``, non-numeric, ``NaN``, infinite, or outside the
        range ``[0, 100]``; or if ``values`` is empty or contains invalid
        entries (bools, non-numeric, ``NaN``, or infinite values).
    """
    if isinstance(p, bool):
        raise ValueError("percentile p must be a number, not a bool")
    if not isinstance(p, (int, float)):
        raise ValueError(
            f"percentile p must be numeric, got {type(p).__name__}"
        )
    p_float = float(p)
    if math.isnan(p_float) or math.isinf(p_float):
        raise ValueError("percentile p must be a finite number")
    if p_float < 0.0 or p_float > 100.0:
        raise ValueError("percentile p must be within the range [0, 100]")

    data = sorted(_to_float_list(values))
    n = len(data)
    if n == 1:
        return data[0]

    rank = (p_float / 100.0) * (n - 1)
    lower_index = int(math.floor(rank))
    upper_index = int(math.ceil(rank))

    if lower_index == upper_index:
        return data[lower_index]

    lower_value = data[lower_index]
    upper_value = data[upper_index]
    fraction = rank - lower_index
    return lower_value + (upper_value - lower_value) * fraction