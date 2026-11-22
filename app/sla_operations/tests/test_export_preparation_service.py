"""Tests for the SLA operations export preparation service.

These tests are deterministic and isolated from core application state.
They exercise ``app.sla_operations.services.export_preparation`` only.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

import pytest

from app.sla_operations.services.export_preparation import (
    ExportPreparationService,
    prepare_export_dataset,
)


class _Color(Enum):
    RED = "red"
    BLUE = "blue"


@pytest.fixture()
def service() -> ExportPreparationService:
    return ExportPreparationService()


@pytest.fixture()
def sample_records() -> list[dict]:
    return [
        {"id": 1, "name": "alpha", "score": 10, "active": True},
        {"id": 2, "name": "beta", "score": 20, "active": False},
        {"id": 3, "name": "gamma", "score": 5, "active": True},
    ]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_prepare_export_dataset_explicit_columns(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, columns=["id", "name"]
    )
    assert result.columns == ["id", "name"]
    assert result.records == [
        {"id": 1, "name": "alpha"},
        {"id": 2, "name": "beta"},
        {"id": 3, "name": "gamma"},
    ]


def test_prepare_export_dataset_header_overrides(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records,
        columns=["id", "name"],
        headers={"id": "Identifier", "name": "Full Name"},
    )
    assert result.headers == {"id": "Identifier", "name": "Full Name"}


def test_prepare_export_dataset_defaults_for_missing_values(service):
    records = [
        {"id": 1, "name": "alpha"},
        {"id": 2},
    ]
    result = service.prepare_export_dataset(
        records, columns=["id", "name"], default_value="N/A"
    )
    assert result.records == [
        {"id": 1, "name": "alpha"},
        {"id": 2, "name": "N/A"},
    ]


def test_prepare_export_dataset_default_none_for_missing(service):
    records = [{"id": 1}, {"id": 2, "name": "beta"}]
    result = service.prepare_export_dataset(records, columns=["id", "name"])
    assert result.records == [
        {"id": 1, "name": None},
        {"id": 2, "name": "beta"},
    ]


# ---------------------------------------------------------------------------
# Column inference
# ---------------------------------------------------------------------------


def test_column_inference_from_first_appearance(service):
    records = [
        {"b": 1, "a": 2},
        {"c": 3, "a": 4},
        {"b": 5},
    ]
    result = service.prepare_export_dataset(records)
    assert result.columns == ["b", "a", "c"]


def test_column_inference_empty_records(service):
    result = service.prepare_export_dataset([])
    assert result.columns == []
    assert result.records == []


def test_column_inference_empty_dicts(service):
    result = service.prepare_export_dataset([{}, {}])
    assert result.columns == []
    assert result.records == [{}, {}]


# ---------------------------------------------------------------------------
# Exclude handling
# ---------------------------------------------------------------------------


def test_exclude_columns(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, exclude=["active", "score"]
    )
    assert result.columns == ["id", "name"]
    for record in result.records:
        assert set(record.keys()) == {"id", "name"}


def test_exclude_with_explicit_columns(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, columns=["id", "name", "score"], exclude=["score"]
    )
    assert result.columns == ["id", "name"]


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_sort_by_ascending(service, sample_records):
    result = service.prepare_export_dataset(sample_records, sort_by="score")
    assert [r["score"] for r in result.records] == [5, 10, 20]


def test_sort_by_descending(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, sort_by="score", descending=True
    )
    assert [r["score"] for r in result.records] == [20, 10, 5]


def test_sort_by_mixed_value_types(service):
    records = [
        {"id": 1, "value": 10},
        {"id": 2, "value": "abc"},
        {"id": 3, "value": None},
        {"id": 4, "value": 2.5},
    ]
    result = service.prepare_export_dataset(records, sort_by="value")
    # None sorts first, then numbers, then strings (deterministic ordering).
    assert [r["id"] for r in result.records] == [3, 4, 1, 2]


def test_sort_by_missing_key(service):
    records = [{"id": 1, "value": 2}, {"id": 2}, {"id": 3, "value": 1}]
    result = service.prepare_export_dataset(records, sort_by="value")
    assert [r["id"] for r in result.records] == [2, 3, 1]


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def test_max_rows_truncation_after_sorting(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, sort_by="score", max_rows=2
    )
    assert [r["score"] for r in result.records] == [5, 10]


def test_max_rows_larger_than_records(service, sample_records):
    result = service.prepare_export_dataset(sample_records, max_rows=100)
    assert len(result.records) == 3


def test_negative_max_rows_raises(service, sample_records):
    with pytest.raises(ValueError):
        service.prepare_export_dataset(sample_records, max_rows=-1)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_none(service):
    result = service.prepare_export_dataset([{"a": None}], columns=["a"])
    assert result.records == [{"a": None}]


def test_normalize_datetime(service):
    dt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    result = service.prepare_export_dataset([{"a": dt}], columns=["a"])
    assert result.records == [{"a": dt.isoformat()}]


def test_normalize_enum(service):
    result = service.prepare_export_dataset([{"a": _Color.RED}], columns=["a"])
    assert result.records == [{"a": "red"}]


def test_normalize_decimal(service):
    result = service.prepare_export_dataset(
        [{"a": Decimal("1.50")}], columns=["a"]
    )
    assert result.records == [{"a": "1.50"}]


def test_normalize_nested_dict_and_list(service):
    record = {"a": {"x": 1, "y": [1, 2]}, "b": [1, {"z": 2}]}
    result = service.prepare_export_dataset([record], columns=["a", "b"])
    assert result.records == [
        {"a": {"x": 1, "y": [1, 2]}, "b": [1, {"z": 2}]}
    ]


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


def test_to_records(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, columns=["id", "name"]
    )
    assert result.to_records() == [
        {"id": 1, "name": "alpha"},
        {"id": 2, "name": "beta"},
        {"id": 3, "name": "gamma"},
    ]


def test_to_csv(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, columns=["id", "name"]
    )
    output = result.to_csv()
    reader = csv.reader(io.StringIO(output))
    rows = list(reader)
    assert rows[0] == ["id", "name"]
    assert rows[1] == ["1", "alpha"]
    assert rows[3] == ["3", "gamma"]


def test_to_csv_uses_headers(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records,
        columns=["id", "name"],
        headers={"id": "Identifier", "name": "Full Name"},
    )
    reader = csv.reader(io.StringIO(result.to_csv()))
    rows = list(reader)
    assert rows[0] == ["Identifier", "Full Name"]


def test_to_json(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, columns=["id", "name"]
    )
    parsed = json.loads(result.to_json())
    assert parsed == [
        {"id": 1, "name": "alpha"},
        {"id": 2, "name": "beta"},
        {"id": 3, "name": "gamma"},
    ]


def test_to_dict(service, sample_records):
    result = service.prepare_export_dataset(
        sample_records, columns=["id", "name"]
    )
    payload = result.to_dict()
    assert payload["columns"] == ["id", "name"]
    assert payload["records"] == [
        {"id": 1, "name": "alpha"},
        {"id": 2, "name": "beta"},
        {"id": 3, "name": "gamma"},
    ]


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def test_module_level_prepare_export_dataset(sample_records):
    result = prepare_export_dataset(sample_records, columns=["id"])
    assert result.columns == ["id"]
    assert result.records == [{"id": 1}, {"id": 2}, {"id": 3}]