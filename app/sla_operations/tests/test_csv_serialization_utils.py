"""Tests for the SLA operations workspace CSV serialization utility.

These tests are deterministic and isolated from core application state.
They exercise the workspace-local ``app.sla_operations.utils.csv_serialization``
module only.
"""

from __future__ import annotations

import csv
import io

import pytest

from app.sla_operations.utils.csv_serialization import (
    serialize_rows,
    serialize_to_csv,
)


def _parse_csv(text: str) -> list[list[str]]:
    """Parse CSV text back into rows for round-trip assertions."""
    return list(csv.reader(io.StringIO(text)))


class TestSerializeRowsBasic:
    """Primary behavior: serializing simple rows."""

    def test_single_row_of_strings(self) -> None:
        result = serialize_rows([["a", "b", "c"]])
        assert result == "a,b,c\r\n"

    def test_multiple_rows(self) -> None:
        result = serialize_rows([["a", "b"], ["c", "d"]])
        assert result == "a,b\r\nc,d\r\n"

    def test_numeric_values_are_stringified(self) -> None:
        result = serialize_rows([[1, 2.5, 3]])
        assert _parse_csv(result) == [["1", "2.5", "3"]]

    def test_none_values_serialize_as_empty(self) -> None:
        result = serialize_rows([["a", None, "c"]])
        assert _parse_csv(result) == [["a", "", "c"]]

    def test_round_trip_preserves_values(self) -> None:
        rows = [["alpha", "beta"], ["gamma", "delta"]]
        assert _parse_csv(serialize_rows(rows)) == rows


class TestSerializeRowsEmpty:
    """Empty input handling."""

    def test_empty_list_returns_empty_string(self) -> None:
        assert serialize_rows([]) == ""

    def test_list_of_empty_rows(self) -> None:
        result = serialize_rows([[], []])
        assert result == "\r\n\r\n"

    def test_empty_row_round_trip(self) -> None:
        assert _parse_csv(serialize_rows([[]])) == [[]]


class TestSerializeRowsEscaping:
    """Escaping of special characters."""

    def test_comma_is_quoted(self) -> None:
        result = serialize_rows([["a,b", "c"]])
        assert _parse_csv(result) == [["a,b", "c"]]
        assert '"a,b"' in result

    def test_double_quote_is_escaped(self) -> None:
        result = serialize_rows([['he said "hi"', "x"]])
        assert _parse_csv(result) == [['he said "hi"', "x"]]
        assert '""' in result

    def test_embedded_newline_is_quoted(self) -> None:
        result = serialize_rows([["line1\nline2", "x"]])
        assert _parse_csv(result) == [["line1\nline2", "x"]]

    def test_embedded_carriage_return_is_quoted(self) -> None:
        result = serialize_rows([["line1\rline2", "x"]])
        assert _parse_csv(result) == [["line1\rline2", "x"]]

    def test_combined_special_characters(self) -> None:
        value = 'a,b "quoted"\nnewline'
        result = serialize_rows([[value]])
        assert _parse_csv(result) == [[value]]


class TestSerializeToCsvDictRows:
    """Header handling for dict rows."""

    def test_dict_rows_emit_header(self) -> None:
        rows = [{"name": "alice", "age": 30}]
        result = serialize_to_csv(rows)
        parsed = _parse_csv(result)
        assert parsed[0] == ["name", "age"]
        assert parsed[1] == ["alice", "30"]

    def test_dict_rows_preserve_key_order(self) -> None:
        rows = [{"b": "2", "a": "1", "c": "3"}]
        parsed = _parse_csv(serialize_to_csv(rows))
        assert parsed[0] == ["b", "a", "c"]

    def test_dict_rows_multiple_records(self) -> None:
        rows = [
            {"id": 1, "status": "open"},
            {"id": 2, "status": "closed"},
        ]
        parsed = _parse_csv(serialize_to_csv(rows))
        assert parsed == [
            ["id", "status"],
            ["1", "open"],
            ["2", "closed"],
        ]

    def test_dict_rows_missing_keys_become_empty(self) -> None:
        rows = [{"a": "1", "b": "2"}, {"a": "3"}]
        parsed = _parse_csv(serialize_to_csv(rows))
        assert parsed == [["a", "b"], ["1", "2"], ["3", ""]]

    def test_dict_rows_escaping(self) -> None:
        rows = [{"note": 'has, comma and "quote"'}]
        parsed = _parse_csv(serialize_to_csv(rows))
        assert parsed == [["note"], ['has, comma and "quote"']]

    def test_empty_dict_rows_returns_empty_string(self) -> None:
        assert serialize_to_csv([]) == ""

    def test_explicit_fieldnames(self) -> None:
        rows = [{"a": "1", "b": "2"}]
        result = serialize_to_csv(rows, fieldnames=["b", "a"])
        parsed = _parse_csv(result)
        assert parsed == [["b", "a"], ["2", "1"]]

    def test_explicit_fieldnames_with_missing_key(self) -> None:
        rows = [{"a": "1"}]
        result = serialize_to_csv(rows, fieldnames=["a", "b"])
        parsed = _parse_csv(result)
        assert parsed == [["a", "b"], ["1", ""]]


class TestSerializeToCsvSequenceRows:
    """Sequence rows passed to serialize_to_csv."""

    def test_sequence_rows_without_header(self) -> None:
        result = serialize_to_csv([["a", "b"], ["c", "d"]])
        assert _parse_csv(result) == [["a", "b"], ["c", "d"]]

    def test_sequence_rows_with_header(self) -> None:
        result = serialize_to_csv([["a", "b"]], header=["col1", "col2"])
        assert _parse_csv(result) == [["col1", "col2"], ["a", "b"]]

    def test_empty_sequence_returns_empty_string(self) -> None:
        assert serialize_to_csv([]) == ""


class TestSerializeToCsvErrors:
    """Error handling for invalid inputs."""

    def test_mixed_row_types_raise(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            serialize_to_csv([{"a": "1"}, ["b"]])  # type: ignore[list-item]

    def test_non_iterable_rows_raise(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            serialize_to_csv([42])  # type: ignore[list-item]