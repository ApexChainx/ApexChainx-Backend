"""Tests for the SLA operations workspace sorting utilities.

These tests are intentionally isolated from the core application: they use
plain pytest, no database, and only import from
``app.sla_operations.utils.sorting``.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.sla_operations.utils.sorting import (
    SortingError,
    sort_records,
)


def _records() -> List[Dict[str, Any]]:
    """Return a fresh list of records used across tests."""
    return [
        {"id": 1, "priority": 3, "name": "alpha"},
        {"id": 2, "priority": 1, "name": "bravo"},
        {"id": 3, "priority": 2, "name": "charlie"},
        {"id": 4, "priority": 2, "name": "delta"},
    ]


def test_sort_ascending_by_key() -> None:
    result = sort_records(_records(), key="priority", direction="asc")
    assert [r["priority"] for r in result] == [1, 2, 2, 3]


def test_sort_descending_by_key() -> None:
    result = sort_records(_records(), key="priority", direction="desc")
    assert [r["priority"] for r in result] == [3, 2, 2, 1]


def test_sort_is_stable_for_equal_keys() -> None:
    records = _records()
    result = sort_records(records, key="priority", direction="asc")
    # Records with priority == 2 must retain their input order (charlie, delta).
    equal = [r["name"] for r in result if r["priority"] == 2]
    assert equal == ["charlie", "delta"]


def test_none_values_placed_last_by_default() -> None:
    records = [
        {"id": 1, "priority": 2},
        {"id": 2, "priority": None},
        {"id": 3, "priority": 1},
    ]
    result = sort_records(records, key="priority", direction="asc")
    assert [r["priority"] for r in result] == [1, 2, None]


def test_none_values_placed_first_when_none_last_false() -> None:
    records = [
        {"id": 1, "priority": 2},
        {"id": 2, "priority": None},
        {"id": 3, "priority": 1},
    ]
    result = sort_records(
        records, key="priority", direction="asc", none_last=False
    )
    assert [r["priority"] for r in result] == [None, 1, 2]


def test_mixed_type_values_produce_deterministic_order() -> None:
    records = [
        {"id": 1, "value": 10},
        {"id": 2, "value": "abc"},
        {"id": 3, "value": 2},
        {"id": 4, "value": None},
    ]
    first = sort_records(records, key="value", direction="asc")
    second = sort_records(records, key="value", direction="asc")
    # Deterministic: repeated calls yield the same ordering.
    assert [r["id"] for r in first] == [r["id"] for r in second]
    # None is still placed last.
    assert first[-1]["value"] is None


def test_empty_list_returns_empty_list() -> None:
    assert sort_records([], key="priority", direction="asc") == []


def test_missing_key_raises_sorting_error() -> None:
    records = [{"id": 1}, {"id": 2}]
    with pytest.raises(SortingError):
        sort_records(records, key="priority", direction="asc")


def test_invalid_direction_raises_sorting_error() -> None:
    with pytest.raises(SortingError):
        sort_records(_records(), key="priority", direction="sideways")


def test_input_list_is_not_mutated() -> None:
    records = _records()
    snapshot = [dict(r) for r in records]
    sort_records(records, key="priority", direction="desc")
    assert records == snapshot