"""Generic, pure aggregation helpers for the SLA operations workspace.

This module provides dependency-light, reusable aggregation utilities
(counts, sums, averages, and group-by) that operate over arbitrary
iterables. The helpers are intentionally independent of any application
state, database, or framework so they can be safely reused by workspace
services, schemas, and reporting code.

All functions are pure: they do not mutate their inputs and produce
deterministic results for a given input.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar

__all__ = [
    "count_values",
    "sum_values",
    "average",
    "group_by",
]

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


def _ensure_iterable(items: Iterable[T]) -> Iterable[T]:
    """Validate that ``items`` is iterable and return it unchanged.

    Parameters:
        items: The candidate iterable.

    Returns:
        The same object, typed as an iterable.

    Raises:
        TypeError: If ``items`` is not iterable (e.g. ``None`` or an int).
    """
    if items is None:
        raise TypeError("items must be an iterable, got None")
    try:
        iter(items)
    except TypeError as exc:
        raise TypeError(
            f"items must be an iterable, got {type(items).__name__!r}"
        ) from exc
    return items


def _is_number(value: Any) -> bool:
    """Return ``True`` if ``value`` is a real numeric value (not bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def count_values(
    items: Iterable[T],
    predicate: Callable[[T], bool] | None = None,
) -> int:
    """Count items in an iterable, optionally filtered by a predicate.

    Parameters:
        items: Any iterable of values to count.
        predicate: Optional callable applied to each item. Only items for
            which ``predicate(item)`` returns a truthy value are counted.
            When ``None`` (default), every item is counted.

    Returns:
        The number of matching items. Returns ``0`` for an empty iterable.

    Raises:
        TypeError: If ``items`` is not iterable, or if ``predicate`` is
            provided but is not callable.

    Edge behavior:
        - Empty input yields ``0``.
        - ``predicate`` results are evaluated for truthiness, so any
          truthy return value counts the item.
    """
    _ensure_iterable(items)
    if predicate is not None and not callable(predicate):
        raise TypeError("predicate must be callable or None")

    if predicate is None:
        return sum(1 for _ in items)
    return sum(1 for item in items if predicate(item))


def sum_values(
    items: Iterable[T],
    value_extractor: Callable[[T], float] | None = None,
) -> float:
    """Sum numeric values extracted from an iterable.

    Parameters:
        items: Any iterable of values to sum.
        value_extractor: Optional callable mapping each item to a numeric
            value. When ``None`` (default), items are assumed to be numeric
            themselves.

    Returns:
        The total as a ``float``. Returns ``0.0`` for an empty iterable.

    Raises:
        TypeError: If ``items`` is not iterable, if ``value_extractor`` is
            provided but is not callable, or if any extracted value is not
            a real number (``int``/``float``, excluding ``bool``).

    Edge behavior:
        - Empty input yields ``0.0``.
        - ``None`` and non-numeric values are rejected with ``TypeError``
          rather than silently skipped, so callers can detect bad data.
        - Booleans are rejected as non-numeric to avoid surprising sums.
    """
    _ensure_iterable(items)
    if value_extractor is not None and not callable(value_extractor):
        raise TypeError("value_extractor must be callable or None")

    total = 0.0
    for item in items:
        value = item if value_extractor is None else value_extractor(item)
        if not _is_number(value):
            raise TypeError(
                "sum_values encountered a non-numeric value: "
                f"{value!r} ({type(value).__name__})"
            )
        total += float(value)
    return total


def average(
    items: Iterable[T],
    value_extractor: Callable[[T], float] | None = None,
) -> float | None:
    """Compute the arithmetic mean of numeric values from an iterable.

    Parameters:
        items: Any iterable of values to average.
        value_extractor: Optional callable mapping each item to a numeric
            value. When ``None`` (default), items are assumed to be numeric
            themselves.

    Returns:
        The mean as a ``float``, or ``None`` for an empty iterable (the
        average of no values is undefined).

    Raises:
        TypeError: If ``items`` is not iterable, if ``value_extractor`` is
            provided but is not callable, or if any extracted value is not
            a real number (``int``/``float``, excluding ``bool``).

    Edge behavior:
        - Empty input yields ``None``.
        - ``None`` and non-numeric values are rejected with ``TypeError``.
        - Booleans are rejected as non-numeric.
    """
    _ensure_iterable(items)
    if value_extractor is not None and not callable(value_extractor):
        raise TypeError("value_extractor must be callable or None")

    total = 0.0
    count = 0
    for item in items:
        value = item if value_extractor is None else value_extractor(item)
        if not _is_number(value):
            raise TypeError(
                "average encountered a non-numeric value: "
                f"{value!r} ({type(value).__name__})"
            )
        total += float(value)
        count += 1

    if count == 0:
        return None
    return total / count


def group_by(
    items: Iterable[T],
    key_extractor: Callable[[T], K],
    value_extractor: Callable[[T], V] | None = None,
) -> dict[K, list[V]]:
    """Group items into lists keyed by a computed key.

    Parameters:
        items: Any iterable of values to group.
        key_extractor: Callable mapping each item to a hashable grouping
            key. Required.
        value_extractor: Optional callable mapping each item to the value
            stored in the group. When ``None`` (default), the original item
            is stored.

    Returns:
        A ``dict`` mapping each key to the list of values for items that
        produced that key. The dict preserves first-seen key order, and each
        group preserves input order, making the result deterministic.

    Raises:
        TypeError: If ``items`` is not iterable, if ``key_extractor`` is not
            callable, if ``value_extractor`` is provided but not callable,
            or if a computed key is unhashable.

    Edge behavior:
        - Empty input yields an empty ``dict``.
        - Keys may be any hashable value, including ``None``.
        - Unhashable keys raise ``TypeError``.
    """
    _ensure_iterable(items)
    if not callable(key_extractor):
        raise TypeError("key_extractor must be callable")
    if value_extractor is not None and not callable(value_extractor):
        raise TypeError("value_extractor must be callable or None")

    grouped: dict[K, list[V]] = {}
    for item in items:
        key = key_extractor(item)
        try:
            hash(key)
        except TypeError as exc:
            raise TypeError(
                f"group_by produced an unhashable key: {key!r} "
                f"({type(key).__name__})"
            ) from exc
        value = item if value_extractor is None else value_extractor(item)
        grouped.setdefault(key, []).append(value)  # type: ignore[arg-type]
    return grouped