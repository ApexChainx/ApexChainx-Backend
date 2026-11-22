"""Sorting utilities for the SLA operations workspace.

This module provides deterministic, stable sorting helpers used by the SLA
operations workspace. It is intentionally self-contained and only depends on
workspace-local modules so that it can be exercised in isolation from the core
application.

Ordering rules
--------------
``sort_items`` applies the following rules, in order:

1. ``None`` values are always grouped together. By default they are placed at
   the end of the result (``none_last=True``); when ``none_last=False`` they are
   placed at the beginning. This placement is independent of ``direction`` so
   that ``None`` handling is predictable regardless of ascending/descending
   order.
2. Non-``None`` values are grouped by their type name (e.g. ``"int"``,
   ``"str"``). Groups are ordered by type name so that mixed-type inputs sort
   deterministically. Within a group, values are compared using their natural
   ordering.
3. If values within a type group are not mutually orderable (e.g. custom
   objects without rich comparison), comparison falls back to the ``str``
   representation of the values.
4. The sort is stable: items that compare equal retain their original relative
   order.

The input sequence is never mutated; a new list is always returned.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional, Sequence, TypeVar, Union

try:  # pragma: no cover - import shim for workspace-local enum
    from app.sla_operations.schemas.pagination import SortDirection
except Exception:  # pragma: no cover - fallback when enum is unavailable
    SortDirection = None  # type: ignore[assignment]


T = TypeVar("T")


class SortingError(ValueError):
    """Raised when a sorting operation cannot be performed.

    Subclasses :class:`ValueError` so callers may catch either the specific
    error or the broader built-in exception.
    """


def _normalize_direction(direction: Union[str, "SortDirection", None]) -> str:
    """Normalize a direction argument to ``"asc"`` or ``"desc"``.

    Accepts the string values ``"asc"``/``"desc"`` (case-insensitive) as well
    as a workspace-local ``SortDirection`` enum member. Raises
    :class:`SortingError` for any other value.
    """
    if direction is None:
        return "asc"

    # Handle enum members (works for str-based enums and plain enums).
    value: Any = direction
    if SortDirection is not None and isinstance(direction, SortDirection):
        value = getattr(direction, "value", direction)

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("asc", "desc"):
            return normalized

    raise SortingError(
        f"Unknown sort direction: {direction!r}. Expected 'asc' or 'desc'."
    )


def _type_group_name(value: Any) -> str:
    """Return a deterministic grouping name for a value's type."""
    return type(value).__name__


def _safe_compare(a: Any, b: Any) -> int:
    """Compare two values, falling back to string comparison if needed.

    Returns a negative, zero, or positive integer following the convention of
    :func:`cmp`. Values that cannot be compared directly are compared by their
    ``str`` representation.
    """
    try:
        if a < b:
            return -1
        if a > b:
            return 1
        return 0
    except TypeError:
        sa, sb = str(a), str(b)
        if sa < sb:
            return -1
        if sa > sb:
            return 1
        return 0


def _make_sort_key(
    key: Union[str, Callable[[Any], Any], None],
    direction: str,
    none_last: bool,
) -> Callable[[Any], tuple]:
    """Build a sort key function implementing the documented ordering rules."""

    def extract(item: Any) -> Any:
        if key is None:
            return item
        if callable(key):
            return key(item)
        if isinstance(key, str):
            if isinstance(item, dict):
                if key not in item:
                    raise SortingError(
                        f"Missing key {key!r} in item: {item!r}"
                    )
                return item[key]
            try:
                return getattr(item, key)
            except AttributeError as exc:
                raise SortingError(
                    f"Missing attribute {key!r} on item: {item!r}"
                ) from exc
        raise SortingError(
            f"Invalid key: {key!r}. Expected a string, callable, or None."
        )

    def sort_key(item: Any) -> tuple:
        value = extract(item)

        if value is None:
            # None group: rank 0 when placed first, rank 2 when placed last.
            none_rank = 2 if none_last else 0
            return (none_rank, "", 0, "")

        group = _type_group_name(value)
        # Non-None group rank sits between the two None ranks.
        group_rank = 1

        # For descending order we invert the comparison result. Because Python
        # sorts ascending, we negate the comparison by using a wrapper that
        # reverses ordering. We achieve this by returning a tuple whose
        # comparison-relevant component is negated via a small helper object.
        return (group_rank, group, _Comparable(value, direction), "")

    return sort_key


class _Comparable:
    """Wrapper that reverses comparison order for descending sorts.

    This keeps the sort key a plain tuple while allowing per-value direction
    handling and the string fallback for unorderable values.
    """

    __slots__ = ("value", "direction")

    def __init__(self, value: Any, direction: str) -> None:
        self.value = value
        self.direction = direction

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Comparable):
            return NotImplemented
        return _safe_compare(self.value, other.value) == 0

    def __lt__(self, other: "_Comparable") -> bool:
        result = _safe_compare(self.value, other.value)
        if self.direction == "desc":
            return result > 0
        return result < 0

    def __le__(self, other: "_Comparable") -> bool:
        return self == other or self < other

    def __gt__(self, other: "_Comparable") -> bool:
        return not self <= other

    def __ge__(self, other: "_Comparable") -> bool:
        return not self < other

    def __hash__(self) -> int:  # pragma: no cover - not used by sorting
        return hash((type(self.value).__name__, str(self.value)))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_Comparable({self.value!r}, {self.direction!r})"


def sort_items(
    items: Iterable[T],
    key: Union[str, Callable[[T], Any], None] = None,
    direction: Union[str, "SortDirection"] = "asc",
    none_last: bool = True,
) -> List[T]:
    """Return a new list of ``items`` sorted according to the module rules.

    Parameters
    ----------
    items:
        An iterable of items to sort. The input is not mutated.
    key:
        Either a string (dict key or attribute name), a callable that extracts a
        sort value from each item, or ``None`` to sort the items themselves.
    direction:
        ``"asc"`` or ``"desc"`` (case-insensitive), or a workspace-local
        ``SortDirection`` enum member. Defaults to ``"asc"``.
    none_last:
        When ``True`` (default), ``None`` values are placed at the end of the
        result regardless of ``direction``. When ``False`` they are placed at
        the beginning.

    Returns
    -------
    list
        A new, stably sorted list.

    Raises
    ------
    SortingError
        If ``direction`` is unknown, ``key`` is an empty string, a required key
        is missing from an item, or ``key`` is of an unsupported type.
    """
    if isinstance(key, str) and key == "":
        raise SortingError("Sort key must not be an empty string.")

    normalized_direction = _normalize_direction(direction)

    # Materialize the input so we can iterate multiple times without mutating.
    materialized: List[T] = list(items)

    sort_key = _make_sort_key(key, normalized_direction, none_last)

    # Pre-validate keys so that missing-key errors are raised deterministically
    # before sorting begins (and independent of sort internals).
    for item in materialized:
        # Calling the key function validates extraction; the returned tuple is
        # discarded here.
        sort_key(item)

    return sorted(materialized, key=sort_key)


__all__ = ["SortingError", "sort_items"]