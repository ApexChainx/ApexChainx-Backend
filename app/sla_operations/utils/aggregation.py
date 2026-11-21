from collections.abc import Iterable, Mapping
from typing import Any, Dict, Hashable, List, Sequence

__all__ = [
    "sum_values",
    "count_by",
    "group_by",
    "bucket_by",
]


def _validate_records(records: Iterable[Mapping]) -> List[Mapping]:
    """Validate that ``records`` is an iterable of mappings.

    Returns the materialized list of records so callers can iterate multiple
    times without re-consuming a generator.
    """
    if records is None:
        raise TypeError("records must be an iterable of mappings, got None")
    if isinstance(records, (str, bytes)):
        raise TypeError("records must be an iterable of mappings, not a string")
    if not isinstance(records, Iterable):
        raise TypeError(
            f"records must be an iterable of mappings, got {type(records).__name__}"
        )

    materialized: List[Mapping] = []
    for index, item in enumerate(records):
        if not isinstance(item, Mapping):
            raise TypeError(
                f"record at index {index} must be a mapping, "
                f"got {type(item).__name__}"
            )
        materialized.append(item)
    return materialized


def _get_value(record: Mapping, key: Hashable, index: int) -> Any:
    """Fetch ``key`` from ``record`` or raise a descriptive ``KeyError``."""
    try:
        return record[key]
    except KeyError:
        raise KeyError(
            f"record at index {index} is missing required key {key!r}"
        ) from None


def sum_values(records: Iterable[Mapping], key: Hashable) -> float:
    """Sum the numeric values stored under ``key`` across ``records``.

    Returns ``0`` for empty input. Raises ``TypeError`` for non-iterable
    records or non-mapping items, ``KeyError`` when ``key`` is missing, and
    ``ValueError`` when a value is not numeric.
    """
    materialized = _validate_records(records)
    total: float = 0
    for index, record in enumerate(materialized):
        value = _get_value(record, key, index)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"value at record index {index} for key {key!r} must be "
                f"numeric, got {type(value).__name__}"
            )
        total += value
    return total


def count_by(records: Iterable[Mapping], key: Hashable) -> Dict[Any, int]:
    """Count occurrences of each distinct value stored under ``key``.

    Returns an empty dict for empty input. Raises ``TypeError`` for
    non-iterable records or non-mapping items and ``KeyError`` when ``key``
    is missing.
    """
    materialized = _validate_records(records)
    counts: Dict[Any, int] = {}
    for index, record in enumerate(materialized):
        value = _get_value(record, key, index)
        counts[value] = counts.get(value, 0) + 1
    return counts


def group_by(records: Iterable[Mapping], key: Hashable) -> Dict[Any, List[Mapping]]:
    """Group records by the value stored under ``key``.

    Returns an empty dict for empty input. Raises ``TypeError`` for
    non-iterable records or non-mapping items and ``KeyError`` when ``key``
    is missing.
    """
    materialized = _validate_records(records)
    groups: Dict[Any, List[Mapping]] = {}
    for index, record in enumerate(materialized):
        value = _get_value(record, key, index)
        groups.setdefault(value, []).append(record)
    return groups


def bucket_by(
    records: Iterable[Mapping],
    key: Hashable,
    buckets: Sequence[float],
) -> Dict[float, List[Mapping]]:
    """Bucket records by numeric value stored under ``key``.

    ``buckets`` must be a non-empty sequence of ascending numeric upper
    bounds. Each record is assigned to the first bucket whose upper bound is
    greater than or equal to the record's value. Values exceeding the largest
    bound are assigned to that largest bound.

    Returns a dict keyed by each bucket bound (in the provided order) with
    lists of matching records; buckets with no matches map to empty lists.
    Raises ``TypeError`` for invalid records or non-numeric bucket bounds,
    ``ValueError`` for empty or non-ascending bounds or non-numeric record
    values, and ``KeyError`` when ``key`` is missing.
    """
    if buckets is None:
        raise TypeError("buckets must be a non-empty sequence of numbers")
    if isinstance(buckets, (str, bytes)):
        raise TypeError("buckets must be a sequence of numbers, not a string")
    if not isinstance(buckets, Sequence):
        raise TypeError(
            f"buckets must be a sequence of numbers, got {type(buckets).__name__}"
        )
    if len(buckets) == 0:
        raise ValueError("buckets must contain at least one bound")

    bounds: List[float] = []
    for index, bound in enumerate(buckets):
        if isinstance(bound, bool) or not isinstance(bound, (int, float)):
            raise ValueError(
                f"bucket bound at index {index} must be numeric, "
                f"got {type(bound).__name__}"
            )
        bounds.append(bound)
    for index in range(1, len(bounds)):
        if bounds[index] < bounds[index - 1]:
            raise ValueError("buckets must be provided in ascending order")

    materialized = _validate_records(records)
    result: Dict[float, List[Mapping]] = {bound: [] for bound in bounds}

    for index, record in enumerate(materialized):
        value = _get_value(record, key, index)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"value at record index {index} for key {key!r} must be "
                f"numeric, got {type(value).__name__}"
            )
        target = bounds[-1]
        for bound in bounds:
            if value <= bound:
                target = bound
                break
        result[target].append(record)

    return result