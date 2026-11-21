from __future__ import annotations

from typing import Any, Mapping

import pytest

from app.sla_operations.utils.filtering import matches_filters


def _record() -> dict[str, Any]:
    return {
        "id": 1,
        "status": "open",
        "priority": "high",
        "team": "ops",
        "region": "us-east",
    }


def test_single_criterion_matches() -> None:
    record = _record()
    assert matches_filters(record, {"status": "open"}) is True


def test_single_criterion_does_not_match() -> None:
    record = _record()
    assert matches_filters(record, {"status": "closed"}) is False


def test_multiple_criteria_all_match() -> None:
    record = _record()
    criteria = {"status": "open", "priority": "high", "team": "ops"}
    assert matches_filters(record, criteria) is True


def test_multiple_criteria_partial_mismatch() -> None:
    record = _record()
    criteria = {"status": "open", "priority": "low"}
    assert matches_filters(record, criteria) is False


def test_empty_criteria_matches_all() -> None:
    record = _record()
    assert matches_filters(record, {}) is True


def test_missing_key_does_not_match() -> None:
    record = _record()
    assert matches_filters(record, {"nonexistent": "value"}) is False


def test_missing_key_among_matching_criteria_does_not_match() -> None:
    record = _record()
    criteria = {"status": "open", "nonexistent": "value"}
    assert matches_filters(record, criteria) is False


def test_none_value_matches_none_field() -> None:
    record = {"status": None}
    assert matches_filters(record, {"status": None}) is True


def test_non_mapping_record_raises_type_error() -> None:
    with pytest.raises(TypeError):
        matches_filters(["not", "a", "mapping"], {"status": "open"})  # type: ignore[arg-type]


def test_non_mapping_criteria_raises_type_error() -> None:
    record = _record()
    with pytest.raises(TypeError):
        matches_filters(record, ["status", "open"])  # type: ignore[arg-type]


def test_none_criteria_raises_type_error() -> None:
    record = _record()
    with pytest.raises(TypeError):
        matches_filters(record, None)  # type: ignore[arg-type]


def test_mapping_subclass_supported() -> None:
    class CustomMapping(Mapping[str, Any]):
        def __init__(self, data: dict[str, Any]) -> None:
            self._data = data

        def __getitem__(self, key: str) -> Any:
            return self._data[key]

        def __iter__(self):
            return iter(self._data)

        def __len__(self) -> int:
            return len(self._data)

    record = CustomMapping(_record())
    assert matches_filters(record, {"status": "open"}) is True
    assert matches_filters(record, {"status": "closed"}) is False


def test_tests_are_isolated_from_core_state() -> None:
    # Each call operates on its own local record; no shared mutable state.
    first = _record()
    second = _record()
    assert matches_filters(first, {"team": "ops"}) is True
    second["team"] = "dev"
    assert matches_filters(second, {"team": "ops"}) is False
    assert matches_filters(first, {"team": "ops"}) is True
</parameter>