from __future__ import annotations

from functools import cmp_to_key
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

__all__ = [
    "sort_records",
    "make_comparator",
    "multi_key_sort",
]


def _validate_field(field: Any) -> str:
    """Validate that a field name is a non-empty string.

    Parameters:
        field: The candidate field name.

    Returns:
        The field name as a ``str``.

    Raises:
        TypeError: If ``field`` is not a string.
        ValueError: If ``field`` is an empty (or whitespace-only) string.
    """
    if not isinstance(field, str):
        raise TypeError(
            f"field name must be a string, got {type(field).__name__}"
        )
    if not field.strip():
        raise ValueError("field name must be a non-empty string")
    return field


def _validate_descending(descending: Any) -> bool:
    """Validate a ``descending`` flag.

    Parameters:
        descending: The candidate flag.

    Returns:
        The flag as a ``bool``.

    Raises:
        TypeError: If ``descending`` is not a boolean.
    """
    if not isinstance(descending, bool):
        raise TypeError(
            f"descending must be a bool, got {type(descending).__name__}"
        )
    return descending


def _validate_none_last(none_last: Any) -> bool:
    """Validate a ``none_last`` flag.

    Parameters:
        none_last: The candidate flag.

    Returns:
        The flag as a ``bool``.

    Raises:
        TypeError: If ``none_last`` is not a boolean.
    """
    if not isinstance(none_last, bool):
        raise TypeError(
            f"none_last must be a bool, got {type(none_last).__name__}"
        )
    return none_last


def _get_field(record: Any, field: str) -> Any:
    """Extract a field value from a mapping-like record.

    Missing fields are treated as ``None``. Records that are not mapping-like
    (i.e. do not support ``__getitem__``) are rejected.

    Parameters:
        record: A mapping-like object (e.g. ``dict``).
        field: The field name to look up.

    Returns:
        The field value, or ``None`` if the field is absent.

    Raises:
        TypeError: If ``record`` is not mapping-like.
    """
    if isinstance(record, Mapping):
        return record.get(field)
    if hasattr(record, "__getitem__"):
        try:
            return record[field]
        except (KeyError, IndexError):
            return None
    raise TypeError(
        "record must be mapping-like (support __getitem__), "
        f"got {type(record).__name__}"
    )


def _compare_values(
    left: Any,
    right: Any,
    descending: bool,
    none_last: bool,
) -> int:
    """Compare two field values with deterministic ``None`` placement.

    ``None`` values are ordered according to ``none_last`` regardless of
    ``descending``: when ``none_last`` is ``True`` they sort after all
    non-``None`` values, otherwise before them. Non-``None`` values are
    compared with the natural ordering of their types; ``descending`` inverts
    that comparison.

    Parameters:
        left: Left-hand value.
        right: Right-hand value.
        descending: Whether non-``None`` ordering is reversed.
        none_last: Whether ``None`` values sort last.

    Returns:
        ``-1``, ``0`` or ``1``.

    Raises:
        TypeError: If two non-``None`` values are not mutually comparable.
    """
    left_none = left is None
    right_none = right is None

    if left_none and right_none:
        return 0
    if left_none:
        return 1 if none_last else -1
    if right_none:
        return -1 if none_last else 1

    try:
        if left < right:
            result = -1
        elif left > right:
            result = 1
        else:
            result = 0
    except TypeError as exc:
        raise TypeError(
            "values are not mutually comparable: "
            f"{type(left).__name__} vs {type(right).__name__}"
        ) from exc

    return -result if descending else result


def make_comparator(
    field: str,
    descending: bool = False,
    none_last: bool = True,
) -> Callable[[Any, Any], int]:
    """Build a ``cmp``-style comparator over a single record field.

    The returned callable accepts two mapping-like records and returns a
    negative integer, zero, or a positive integer following the semantics of
    ``functools.cmp_to_key``.

    Parameters:
        field: Field name to compare. Must be a non-empty string.
        descending: If ``True``, reverse the ordering of non-``None`` values.
        none_last: If ``True`` (default), ``None`` values sort after all
            non-``None`` values; otherwise they sort before them. This
            placement is independent of ``descending``.

    Returns:
        A comparator ``(left, right) -> int``.

    Raises:
        TypeError: If ``field`` is not a string or the flags are not booleans.
        ValueError: If ``field`` is empty or whitespace-only.

    Edge behavior:
        - Missing fields are treated as ``None``.
        - Records that are not mapping-like raise ``TypeError`` when compared.
        - Values of incompatible types raise ``TypeError`` when compared.
    """
    field = _validate_field(field)
    descending = _validate_descending(descending)
    none_last = _validate_none_last(none_last)

    def comparator(left: Any, right: Any) -> int:
        left_value = _get_field(left, field)
        right_value = _get_field(right, field)
        return _compare_values(left_value, right_value, descending, none_last)

    return comparator


def sort_records(
    records: Optional[Iterable[Any]],
    key: str,
    descending: bool = False,
    none_last: bool = True,
) -> list[Any]:
    """Sort mapping-like records by a single field.

    Parameters:
        records: Iterable of mapping-like records. ``None`` is treated as an
            empty iterable.
        key: Field name to sort by. Must be a non-empty string.
        descending: If ``True``, reverse the ordering of non-``None`` values.
        none_last: If ``True`` (default), records whose field is missing or
            ``None`` sort last; otherwise they sort first. This placement is
            independent of ``descending``.

    Returns:
        A new list of records in sorted order. The input is never mutated.

    Raises:
        TypeError: If ``key`` is not a string, the flags are not booleans, or
            a record is not mapping-like.
        ValueError: If ``key`` is empty or whitespace-only.

    Edge behavior:
        - ``records`` of ``None`` or an empty iterable yields ``[]``.
        - Missing fields are treated as ``None``.
        - Sorting is stable: records comparing equal retain input order.
    """
    key = _validate_field(key)
    descending = _validate_descending(descending)
    none_last = _validate_none_last(none_last)

    if records is None:
        return []

    comparator = make_comparator(key, descending=descending, none_last=none_last)
    return sorted(records, key=cmp_to_key(comparator))


def multi_key_sort(
    records: Optional[Iterable[Any]],
    keys: Sequence[Tuple[str, bool]],
) -> list[Any]:
    """Sort mapping-like records by multiple fields in priority order.

    Fields are applied in the order given: the first tuple is the primary
    sort key, the second breaks ties, and so on. ``None`` values (including
    missing fields) always sort last within each field, independent of that
    field's ``descending`` flag.

    Parameters:
        records: Iterable of mapping-like records. ``None`` is treated as an
            empty iterable.
        keys: Sequence of ``(field, descending)`` tuples. Each ``field`` must
            be a non-empty string and each ``descending`` a boolean.

    Returns:
        A new list of records in sorted order. The input is never mutated.

    Raises:
        TypeError: If ``keys`` is not a sequence of tuples, a field is not a
            string, a ``descending`` flag is not a boolean, or a record is not
            mapping-like.
        ValueError: If a field is empty or whitespace-only.

    Edge behavior:
        - ``records`` of ``None`` or an empty iterable yields ``[]``.
        - An empty ``keys`` sequence yields the records in their original
          order (a shallow copy).
        - Sorting is stable: records comparing equal on all keys retain input
          order.
    """
    if keys is None:
        raise TypeError("keys must be a sequence of (field, descending) tuples")
    if isinstance(keys, (str, bytes)):
        raise TypeError("keys must be a sequence of (field, descending) tuples")

    normalized: list[Tuple[str, bool]] = []
    for entry in keys:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise TypeError(
                "each key must be a (field, descending) tuple, "
                f"got {entry!r}"
            )
        field, descending = entry
        normalized.append(
            (_validate_field(field), _validate_descending(descending))
        )

    if records is None:
        return []

    result = list(records)
    if not normalized:
        return result

    # Apply keys from least to most significant so that Python's stable sort
    # produces the correct multi-field ordering.
    for field, descending in reversed(normalized):
        comparator = make_comparator(
            field, descending=descending, none_last=True
        )
        result.sort(key=cmp_to_key(comparator))

    return result
</parameter>