import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import pytest

from app.sla_operations.services.export_preparation import (
    ExportColumn,
    prepare_export,
    prepare_export_dataset,
)


class Rating(Enum):
    GOOD = "good"


@dataclass
class SlaRow:
    outage_id: str
    amount: int
    rating: Rating
    created_at: datetime


def test_explicit_columns_define_order_headers_and_defaults():
    dataset = prepare_export_dataset(
        [{"outage_id": "outage-1", "amount": 100}],
        columns=[
            ExportColumn(key="amount", header="Payout"),
            "outage_id",
            ExportColumn(key="rating", default="unrated"),
        ],
    )

    assert dataset.headers == ("Payout", "outage_id", "rating")
    assert dataset.to_records() == [
        {"amount": 100, "outage_id": "outage-1", "rating": "unrated"},
    ]
    rows = list(csv.reader(io.StringIO(dataset.to_csv())))
    assert rows == [["Payout", "outage_id", "rating"], ["100", "outage-1", "unrated"]]
    assert json.loads(dataset.to_json()) == dataset.to_records()
    assert dataset.to_dict()["row_count"] == 1


def test_columns_are_inferred_in_first_seen_order_and_excluded_keys_dropped():
    dataset = prepare_export_dataset(
        [{"outage_id": "o-1", "internal": "secret"}, {"amount": 50, "outage_id": "o-2"}],
        exclude=["internal"],
    )

    assert tuple(column.key for column in dataset.columns) == ("outage_id", "amount")
    assert dataset.to_records() == [
        {"outage_id": "o-1", "amount": ""},
        {"outage_id": "o-2", "amount": 50},
    ]


def test_nested_and_special_values_are_normalized_consistently():
    dataset = prepare_export_dataset(
        [
            {
                "occurred": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
                "tags": {"b": 2, "a": 1},
                "rating": Rating.GOOD,
                "payload": b"bytes",
            }
        ]
    )

    record = dataset.to_records()[0]
    assert record["occurred"] == "2026-01-02T03:04:00+00:00"
    assert record["tags"] == '{"a":1,"b":2}'
    assert record["rating"] == "good"
    assert record["payload"] == "bytes"
    assert "bytes" in dataset.to_csv()


def test_sorting_descending_and_max_rows_are_applied_after_normalization():
    records = [
        {"outage_id": "o-1", "amount": 10},
        {"outage_id": "o-2", "amount": 30},
        {"outage_id": "o-3", "amount": None},
    ]

    dataset = prepare_export_dataset(records, sort_by="amount", descending=True, max_rows=2)

    assert [row["outage_id"] for row in dataset.to_records()] == ["o-2", "o-1"]


def test_sorting_handles_mixed_value_types_without_type_errors():
    records = [{"value": "text"}, {"value": 2}, {"value": None}, {"value": 1}]

    dataset = prepare_export_dataset(records, sort_by="value")

    assert [row["value"] for row in dataset.to_records()] == [1, 2, "text", ""]


def test_empty_datasets_render_safely():
    with_columns = prepare_export_dataset([], columns=["outage_id", "amount"])
    without_columns = prepare_export_dataset([])

    assert with_columns.row_count == 0
    assert with_columns.to_csv() == "outage_id,amount\n"
    assert with_columns.to_json() == "[]"
    assert without_columns.to_csv() == ""
    assert without_columns.to_json() == "[]"


def test_zero_max_rows_produces_headers_only():
    dataset = prepare_export_dataset([{"outage_id": "o-1"}], columns=["outage_id"], max_rows=0)

    assert dataset.to_csv() == "outage_id\n"
    assert dataset.to_records() == []


def test_negative_max_rows_is_rejected():
    with pytest.raises(ValueError, match="max_rows"):
        prepare_export_dataset([], max_rows=-1)


def test_duplicate_columns_are_deduplicated_and_name_has_default():
    dataset = prepare_export_dataset(
        [{"outage_id": "o-1"}],
        columns=[{"key": "outage_id", "header": "Outage"}, "outage_id"],
        name="  ",
    )

    assert dataset.name == "sla_operations"
    assert dataset.headers == ("Outage",)
    assert prepare_export is prepare_export_dataset


def test_attribute_objects_and_model_dump_objects_are_supported():
    rows = [SlaRow("o-1", 100, Rating.GOOD, datetime(2026, 2, 1, tzinfo=timezone.utc))]

    class ModelLike:
        def model_dump(self):
            return {"outage_id": "o-2", "amount": 200}

    dataset = prepare_export_dataset(rows + [ModelLike()])

    assert dataset.to_records() == [
        {
            "outage_id": "o-1",
            "amount": 100,
            "rating": "good",
            "created_at": "2026-02-01T00:00:00+00:00",
        },
        {"outage_id": "o-2", "amount": 200},
    ]
