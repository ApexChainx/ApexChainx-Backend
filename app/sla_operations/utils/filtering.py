from __future__ import annotations

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
    "Predicate",
    "SUPPORTED_OPERATORS",
    "build_predicate",
    "combine_predicates",
    "filter_records",
    "apply_filters",
]

#: A predicate is a callable that accepts a single record and returns a bool.
Predicate = Callable[[Mapping[str, Any]], bool]

#: Operators supported by :func:`build_predicate`.
SUPPORTED_OPERATORS: frozenset[str] = frozenset(
    {
        "eq",
        "ne",
        "in",
        "not_in",
        "lt",
        "lte",
        "gt",
        "gte",
        "contains",
        "startswith",
        "endswith",
    }
)

#: Operators that require the predicate value to be a (non-string) iterable.
_ITERABLE_OPERATORS: frozenset[str] = frozenset({"in", "not_in"})

#: Operators that require the predicate value to be a string.
_STRING_OPERATORS: frozenset[str] = frozenset(
    {"contains", "startswith", "endswith"}
)

#: Operators that require the predicate value to be orderable.
_ORDERING_OPERATORS: frozenset[str] = frozenset({"lt", "lte", "gt", "gte"})

#: Sentinel used to distinguish "field missing" from "field is None".
_MISSING: Any = object()


def _resolve_operator(op: str) -> str:
    """Normalize and validate an operator name.

    Parameters
    ----------
    op:
        The operator name supplied by the caller.

    Returns
    -------
    str
        The normalized (lower-cased, stripped) operator name.

    Raises
    ------
    TypeError
        If ``op`` is not a string.
    ValueError
        If ``op`` is not one of :data:`SUPPORTED_OPERATORS`.
    """
    if not isinstance(op, str):
        raise TypeError(
            f"operator must be a string, got {type(op).__name__!r}"
        )
    normalized = op.strip().lower()
    if normalized not in SUPPORTED_OPERATORS:
        raise ValueError(
            f"unsupported operator {op!r}; "
            f"expected one of {sorted(SUPPORTED_OPERATORS)}"
        )
    return normalized


def _validate_operator_value(op: str, value: Any) -> None:
    """Validate that ``value`` is compatible with ``op``.

    Parameters
    ----------
    op:
        A normalized operator name (see :func:`_resolve_operator`).
    value:
        The comparison value supplied to :func:`build_predicate`.

    Raises
    ------
    TypeError
        If the value type is incompatible with the operator.
    """
    if op in _ITERABLE_OPERATORS:
        if isinstance(value, (str, bytes)) or not isinstance(
            value, Iterable
        ):
            raise TypeError(
                f"operator {op!r} requires a non-string iterable value, "
                f"got {type(value).__name__!r}"
            )
    elif op in _STRING_OPERATORS:
        if not isinstance(value, str):
            raise TypeError(
                f"operator {op!r} requires a string value, "
                f"got {type(value).__name__!r}"
            )
    elif op in _ORDERING_OPERATORS:
        # Ordering comparisons are validated lazily at call time because the
        # record value type is unknown until the predicate runs. We only
        # reject obviously non-orderable containers here.
        if isinstance(value, (dict, set, frozenset)):
            raise TypeError(
                f"operator {op!r} requires an orderable value, "
                f"got {type(value).__name__!r}"
            )


def _compare(op: str, actual: Any, expected: Any) -> bool:
    """Apply a single comparison, returning ``False`` on type mismatch.

    Parameters
    ----------
    op:
        A normalized operator name.
    actual:
        The value extracted from the record (may be ``None``).
    expected:
        The value supplied to :func:`build_predicate`.

    Returns
    -------
    bool
        The result of the comparison. Ordering operators return ``False``
        when the two values are not mutually orderable (e.g. ``None`` vs
        ``int``) rather than raising, so that heterogeneous record sets can
        be filtered safely.
    """
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    if op == "contains":
        return isinstance(actual, str) and expected in actual
    if op == "startswith":
        return isinstance(actual, str) and actual.startswith(expected)
    if op == "endswith":
        return isinstance(actual, str) and actual.endswith(expected)

    # Ordering operators.
    if actual is None:
        return False
    try:
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
    except TypeError:
        return False

    # Unreachable: _resolve_operator guarantees a known operator.
    raise ValueError(f"unsupported operator {op!r}")


def build_predicate(
    field: str,
    op: str,
    value: Any,
    *,
    missing_is_none: bool = True,
) -> Predicate:
    """Build a single-field predicate callable.

    Parameters
    ----------
    field:
        The record key to inspect. Must be a non-empty string.
    op:
        The comparison operator. One of :data:`SUPPORTED_OPERATORS`
        (case-insensitive). Unknown operators raise :class:`ValueError`.
    value:
        The comparison value. Its required type depends on ``op``:

        * ``in`` / ``not_in``: a non-string iterable (e.g. ``list``, ``set``).
        * ``contains`` / ``startswith`` / ``endswith``: a ``str``.
        * ``lt`` / ``lte`` / ``gt`` / ``gte``: an orderable value.

        Incompatible value types raise :class:`TypeError`.
    missing_is_none:
        When ``True`` (default), a record that lacks ``field`` is treated as
        if the field were ``None``. When ``False``, records lacking the field
        are excluded (the predicate returns ``False``) regardless of ``op``.

    Returns
    -------
    Predicate
        A callable accepting a mapping-like record and returning ``bool``.

    Raises
    ------
    TypeError
        If ``field`` is not a string, ``op`` is not a string, or ``value`` is
        incompatible with ``op``.
    ValueError
        If ``field`` is empty or ``op`` is unsupported.
    """
    if not isinstance(field, str):
        raise TypeError(
            f"field must be a string, got {type(field).__name__!r}"
        )
    if not field:
        raise ValueError("field must be a non-empty string")

    normalized_op = _resolve_operator(op)
    _validate_operator_value(normalized_op, value)

    def predicate(record: Mapping[str, Any]) -> bool:
        if not isinstance(record, Mapping):
            raise TypeError(
                "record must be a mapping, "
                f"got {type(record).__name__!r}"
            )
        actual = record.get(field, _MISSING)
        if actual is _MISSING:
            if not missing_is_none:
                return False
            actual = None
        return _compare(normalized_op, actual, value)

    predicate.__name__ = f"predicate_{field}_{normalized_op}"
    predicate.__qualname__ = predicate.__name__
    return predicate


def combine_predicates(
    predicates: Optional[Iterable[Predicate]],
    mode: str = "and",
) -> Predicate:
    """Combine several predicates into a single predicate.

    Parameters
    ----------
    predicates:
        An iterable of predicate callables. ``None`` is treated as an empty
        iterable. An empty iterable yields a predicate that always returns
        ``True`` (the identity element for both ``and`` and ``or`` modes).
    mode:
        Combination mode, either ``"and"`` (all must match) or ``"or"`` (any
        must match). Case-insensitive. Any other value raises
        :class:`ValueError`.

    Returns
    -------
    Predicate
        A callable accepting a mapping-like record and returning ``bool``.

    Raises
    ------
    TypeError
        If ``mode`` is not a string or any element of ``predicates`` is not
        callable.
    ValueError
        If ``mode`` is not ``"and"`` or ``"or"``.
    """
    if not isinstance(mode, str):
        raise TypeError(f"mode must be a string, got {type(mode).__name__!r}")
    normalized_mode = mode.strip().lower()
    if normalized_mode not in ("and", "or"):
        raise ValueError(
            f"mode must be 'and' or 'or', got {mode!r}"
        )

    if predicates is None:
        collected: Tuple[Predicate, ...] = ()
    else:
        collected = tuple(predicates)
        for index, pred in enumerate(collected):
            if not callable(pred):
                raise TypeError(
                    f"predicate at index {index} is not callable "
                    f"({type(pred).__name__!r})"
                )

    if normalized_mode == "and":

        def combined_and(record: Mapping[str, Any]) -> bool:
            return all(pred(record) for pred in collected)

        return combined_and

    def combined_or(record: Mapping[str, Any]) -> bool:
        return any(pred(record) for pred in collected)

    return combined_or


def filter_records(
    records: Optional[Iterable[Mapping[str, Any]]],
    predicates: Optional[Iterable[Predicate]],
    mode: str = "and",
    *,
    allowed_fields: Optional[Iterable[str]] = None,
    missing_is_none: bool = True,
) -> list[Mapping[str, Any]]:
    """Filter an in-memory iterable of mapping-like records.

    Parameters
    ----------
    records:
        An iterable of mapping-like records. ``None`` is treated as an empty
        iterable and yields ``[]``.
    predicates:
        Predicates to apply (see :func:`combine_predicates`). ``None`` or an
        empty iterable returns all records unchanged (as a new list).
    mode:
        Combination mode, ``"and"`` or ``"or"`` (case-insensitive).
    allowed_fields:
        Optional whitelist of permitted field names. When supplied, every
        predicate must be a predicate produced by :func:`build_predicate`
        whose ``field`` is in the whitelist; otherwise :class:`ValueError`
        is raised. This guards against typos and unintended field access.
    missing_is_none:
        Forwarded to :func:`build_predicate` when constructing predicates
        internally; retained for API symmetry. It has no effect on
        pre-built predicates.

    Returns
    -------
    list[Mapping[str, Any]]
        A new list containing the records that satisfy the combined
        predicate, preserving input order.

    Raises
    ------
    TypeError
        If any record is not a mapping or any predicate is not callable.
    ValueError
        If ``mode`` is invalid or a predicate references a field outside
        ``allowed_fields``.
    """
    # Validate mode eagerly so empty inputs still surface bad arguments.
    combined = combine_predicates(predicates, mode=mode)

    if allowed_fields is not None:
        allowed = frozenset(allowed_fields)
        for pred in predicates or ():
            field = getattr(pred, "field", None)
            if field is None:
                # Derive the field from the generated predicate name when
                # possible; otherwise we cannot validate it.
                name = getattr(pred, "__name__", "")
                if name.startswith("predicate_"):
                    remainder = name[len("predicate_"):]
                    field = remainder.rsplit("_", 1)[0]
            if field is not None and field not in allowed:
                raise ValueError(
                    f"predicate references unknown field {field!r}; "
                    f"allowed fields are {sorted(allowed)}"
                )

    if records is None:
        return []

    result: list[Mapping[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError(
                "record must be a mapping, "
                f"got {type(record).__name__!r}"
            )
        if combined(record):
            result.append(record)
    return result


def apply_filters(
    records: Optional[Iterable[Mapping[str, Any]]],
    filters: Optional[Sequence[Tuple[str, str, Any]]] = None,
    mode: str = "and",
    *,
    allowed_fields: Optional[Iterable[str]] = None,
    missing_is_none: bool = True,
) -> list[Mapping[str, Any]]:
    """Convenience wrapper: build predicates from tuples and filter records.

    Parameters
    ----------
    records:
        An iterable of mapping-like records. ``None`` is treated as empty.
    filters:
        A sequence of ``(field, op, value)`` tuples, each converted via
        :func:`build_predicate`. ``None`` or an empty sequence returns all
        records (as a new list).
    mode:
        Combination mode, ``"and"`` or ``"or"`` (case-insensitive).
    allowed_fields:
        Optional whitelist of permitted field names (see
        :func:`filter_records`).
    missing_is_none:
        Forwarded to :func:`build_predicate`.

    Returns
    -------
    list[Mapping[str, Any]]
        The filtered records, preserving input order.

    Raises
    ------
    TypeError
        If a filter entry is malformed or a value is incompatible with its
        operator.
    ValueError
        If an operator is unsupported, a field is empty, or a field is not in
        ``allowed_fields``.
    """
    if filters is None:
        predicates: list[Predicate] = []
    else:
        predicates = []
        for index, entry in enumerate(filters):
            if not isinstance(entry, (tuple, list)) or len(entry) != 3:
                raise TypeError(
                    "each filter must be a (field, op, value) tuple; "
                    f"entry at index {index} is {entry!r}"
                )
            field, op, value = entry
            predicates.append(
                build_predicate(
                    field, op, value, missing_is_none=missing_is_none
                )
            )

    return filter_records(
        records,
        predicates,
        mode=mode,
        allowed_fields=allowed_fields,
        missing_is_none=missing_is_none,
    )