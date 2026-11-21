from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _ensure_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping, got {type(value).__name__}")
    return value


def matches_criteria(record: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    """Return True if ``record`` matches all ``criteria`` key/value pairs.

    - An empty ``criteria`` mapping matches everything.
    - A missing key in ``record`` is treated as non-matching.
    - Both ``record`` and ``criteria`` must be mappings; otherwise ``TypeError``.
    """
    record = _ensure_mapping(record, "record")
    criteria = _ensure_mapping(criteria, "criteria")

    for key, expected in criteria.items():
        if key not in record:
            return False
        if record[key] != expected:
            return False
    return True


def filter_records(
    records: Iterable[Mapping[str, Any]],
    criteria: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return a list of records (as dicts) matching all ``criteria``.

    - An empty ``criteria`` mapping returns all records.
    - A missing key in a record is treated as non-matching.
    - ``criteria`` must be a mapping; otherwise ``TypeError``.
    - Each record must be a mapping; otherwise ``TypeError``.
    """
    criteria = _ensure_mapping(criteria, "criteria")

    if isinstance(records, (str, bytes)):
        raise TypeError("records must be an iterable of mappings, not a string")

    result: list[dict[str, Any]] = []
    for record in records:
        record = _ensure_mapping(record, "record")
        if matches_criteria(record, criteria):
            result.append(dict(record))
    return result