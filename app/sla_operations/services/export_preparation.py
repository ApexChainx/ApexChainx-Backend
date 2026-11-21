"""Deterministic dataset preparation for CSV and JSON export workflows.

A prepared dataset fixes column order, normalizes optional values once, and can
then be rendered to either format without re-deriving structure. Nested values
are encoded as compact JSON strings so CSV and JSON exports share one stable
representation.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

DEFAULT_EXPORT_NAME = "sla_operations"


@dataclass(frozen=True)
class ExportColumn:
    key: str
    header: str | None = None
    default: Any = ""


@dataclass(frozen=True)
class ExportDataset:
    name: str
    columns: tuple[ExportColumn, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def headers(self) -> tuple[str, ...]:
        return tuple(column.header or column.key for column in self.columns)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_records(self) -> list[dict[str, Any]]:
        keys = tuple(column.key for column in self.columns)
        return [dict(zip(keys, row)) for row in self.rows]

    def to_csv(self) -> str:
        if not self.columns:
            return ""
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(list(self.headers))
        writer.writerows(self.rows)
        return output.getvalue()

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_records(), ensure_ascii=False, default=str, indent=indent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": [
                {"key": column.key, "header": column.header or column.key} for column in self.columns
            ],
            "rows": self.to_records(),
            "row_count": self.row_count,
        }


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _coerce_record(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump())
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise ValueError("export records must be mappings or objects with attributes")


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _normalize_value(value.value)
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return _normalize_value(sorted(value, key=str))
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return value


def _resolve_columns(
    records: Sequence[Mapping[str, Any]],
    columns: Sequence[Any] | None,
    excluded: set[str],
    default: Any,
) -> tuple[ExportColumn, ...]:
    resolved: list[ExportColumn] = []
    seen: set[str] = set()

    def add(column: ExportColumn) -> None:
        key = str(column.key).strip()
        if not key or key in excluded or key in seen:
            return
        seen.add(key)
        resolved.append(
            ExportColumn(
                key=key,
                header=str(column.header).strip() if column.header is not None else None,
                default=_normalize_value(column.default),
            )
        )

    if columns is not None:
        if isinstance(columns, str):
            columns = [columns]
        for column in columns:
            if isinstance(column, ExportColumn):
                add(column)
            elif isinstance(column, Mapping):
                add(
                    ExportColumn(
                        key=str(column.get("key", "")),
                        header=column.get("header"),
                        default=column.get("default", default),
                    )
                )
            else:
                add(ExportColumn(key=str(column), default=default))
        return tuple(resolved)

    for record in records:
        for key in record:
            add(ExportColumn(key=str(key), default=default))
    return tuple(resolved)


def _sort_key(record: Mapping[str, Any], key: str, default: Any) -> tuple[int, float, str]:
    value = record.get(key, default)
    if value is None:
        return (2, 0.0, "")
    if isinstance(value, bool):
        return (0, float(value), "")
    if isinstance(value, (int, float, Decimal)):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = None
        if number is not None and math.isfinite(number):
            return (0, number, "")
    return (1, 0.0, str(_normalize_value(value)))


def prepare_export_dataset(
    records: Iterable[Any],
    *,
    name: str = DEFAULT_EXPORT_NAME,
    columns: Sequence[Any] | None = None,
    exclude: Iterable[str] = (),
    default: Any = "",
    sort_by: str | None = None,
    descending: bool = False,
    max_rows: int | None = None,
) -> ExportDataset:
    """Build a deterministic export dataset from mappings or attribute objects.

    Column order is explicit when ``columns`` is supplied and otherwise follows
    first appearance across ``records``. Missing values use the column default,
    ``sort_by`` is a stable total ordering across mixed value types, and
    ``max_rows`` is applied after sorting. A negative ``max_rows`` is rejected.
    """
    if max_rows is not None and max_rows < 0:
        raise ValueError("max_rows must be non-negative")

    normalized_records = [_coerce_record(record) for record in records]
    excluded = {str(item) for item in (exclude if not isinstance(exclude, str) else [exclude])}
    resolved_columns = _resolve_columns(normalized_records, columns, excluded, default)

    if sort_by:
        normalized_records.sort(key=lambda record: _sort_key(record, sort_by, default), reverse=descending)
    if max_rows is not None:
        normalized_records = normalized_records[:max_rows]

    rows = tuple(
        tuple(_normalize_value(_read(record, column.key, column.default)) for column in resolved_columns)
        for record in normalized_records
    )
    return ExportDataset(
        name=str(name).strip() or DEFAULT_EXPORT_NAME,
        columns=resolved_columns,
        rows=rows,
    )


prepare_export = prepare_export_dataset
