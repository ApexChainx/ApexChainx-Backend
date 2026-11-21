from __future__ import annotations

from dataclasses import dataclass, asdict
from math import sqrt
from typing import Dict, Iterable, List, Sequence, Union

Number = Union[int, float]


def _to_list(values: Iterable[Number]) -> List[Number]:
    """Materialize an iterable of numbers, validating each element.

    Raises:
        TypeError: If any element is not an int or float (bool excluded).
        ValueError: If the sequence is empty.
    """
    if isinstance(values, (str, bytes)):
        raise TypeError("values must be an iterable of numbers, not a string")

    try:
        items = list(values)
    except TypeError as exc:
        raise TypeError("values must be an iterable of numbers") from exc

    if not items:
        raise ValueError("values must not be empty")

    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TypeError(
                f"all values must be numeric, got {type(item).__name__}"
            )

    return items


def mean(values: Iterable[Number]) -> float:
    """Return the arithmetic mean of ``values``.

    Raises:
        TypeError: If any value is non-numeric.
        ValueError: If ``values`` is empty.
    """
    items = _to_list(values)
    return sum(items) / len(items)


def median(values: Iterable[Number]) -> float:
    """Return the median of ``values``.

    For an even number of values, returns the average of the two middle values.

    Raises:
        TypeError: If any value is non-numeric.
        ValueError: If ``values`` is empty.
    """
    items = sorted(_to_list(values))
    n = len(items)
    mid = n // 2
    if n % 2 == 1:
        return float(items[mid])
    return (items[mid - 1] + items[mid]) / 2.0


def percentile(values: Iterable[Number], p: float) -> float:
    """Return the ``p``-th percentile of ``values`` using linear interpolation.

    ``p`` must be within the inclusive range [0, 100]. Uses the same
    interpolation method as ``numpy.percentile`` with ``method="linear"``.

    Raises:
        TypeError: If any value is non-numeric or ``p`` is not numeric.
        ValueError: If ``values`` is empty or ``p`` is outside [0, 100].
    """
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        raise TypeError("p must be numeric")
    if p < 0 or p > 100:
        raise ValueError("p must be between 0 and 100 inclusive")

    items = sorted(_to_list(values))
    n = len(items)
    if n == 1:
        return float(items[0])

    rank = (p / 100.0) * (n - 1)
    lower_index = int(rank)
    upper_index = min(lower_index + 1, n - 1)
    fraction = rank - lower_index

    lower = items[lower_index]
    upper = items[upper_index]
    return float(lower + (upper - lower) * fraction)


def stddev(values: Iterable[Number], sample: bool = True) -> float:
    """Return the standard deviation of ``values``.

    Args:
        values: Sequence of numbers.
        sample: If True (default), compute the sample standard deviation
            (denominator ``n - 1``). If False, compute the population
            standard deviation (denominator ``n``).

    Raises:
        TypeError: If any value is non-numeric.
        ValueError: If ``values`` is empty, or if ``sample`` is True and
            fewer than two values are provided.
    """
    items = _to_list(values)
    n = len(items)

    if sample and n < 2:
        raise ValueError("sample standard deviation requires at least two values")

    avg = sum(items) / n
    squared_diffs = sum((x - avg) ** 2 for x in items)
    denominator = (n - 1) if sample else n
    return sqrt(squared_diffs / denominator)


def minimum(values: Iterable[Number]) -> Number:
    """Return the smallest value in ``values``.

    Raises:
        TypeError: If any value is non-numeric.
        ValueError: If ``values`` is empty.
    """
    return min(_to_list(values))


def maximum(values: Iterable[Number]) -> Number:
    """Return the largest value in ``values``.

    Raises:
        TypeError: If any value is non-numeric.
        ValueError: If ``values`` is empty.
    """
    return max(_to_list(values))


@dataclass(frozen=True)
class StatisticsSummary:
    """Aggregated statistics for a sequence of numbers."""

    count: int
    mean: float
    median: float
    minimum: Number
    maximum: Number
    stddev: float
    p25: float
    p50: float
    p75: float

    def as_dict(self) -> Dict[str, Number]:
        """Return the summary as a plain dictionary."""
        return asdict(self)


def summary(values: Sequence[Number]) -> StatisticsSummary:
    """Return a :class:`StatisticsSummary` for ``values``.

    Raises:
        TypeError: If any value is non-numeric.
        ValueError: If ``values`` is empty.
    """
    items = _to_list(values)
    return StatisticsSummary(
        count=len(items),
        mean=mean(items),
        median=median(items),
        minimum=minimum(items),
        maximum=maximum(items),
        stddev=stddev(items, sample=True),
        p25=percentile(items, 25),
        p50=percentile(items, 50),
        p75=percentile(items, 75),
    )