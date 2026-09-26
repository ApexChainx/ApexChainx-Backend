"""Streamed outage imports must validate every row and import all or nothing (#547).

The streaming path used to require only `site_id`, so a row with a garbage
`detected_at` or a nonsense `severity` counted as imported, and the failure
`index` it reported was the *chunk's* start rather than the row's own position.
These tests pin the typed contract, the row numbers, and reject-or-nothing.
"""

import json

import pytest

from app.models.outage_dto import ImportConsistency
from app.services.outage_stream_import import stream_import_outages


def _row(**overrides):
    row = {
        "id": "outage-001",
        "site_name": "Site A",
        "site_id": "site-A",
        "severity": "high",
        "status": "open",
        "detected_at": "2026-01-01T00:00:00Z",
        "description": "Core API degraded",
        "affected_services": ["core-api"],
    }
    row.update(overrides)
    return row


def _body(*rows):
    return json.dumps(list(rows)).encode()


@pytest.fixture
def db():
    """The module takes a session for the future persistence step; it is unused."""
    yield None


class TestValidRows:
    def test_all_valid_rows_are_imported(self, db):
        result = stream_import_outages(db, _body(_row(), _row(id="outage-002")))

        assert result["imported"] == 2
        assert result["failed_count"] == 0
        assert result["valid_row_ids"] == ["outage-001", "outage-002"]
        assert result["total_rows"] == 2
        assert "error" not in result

    def test_empty_body_is_not_an_error(self, db):
        result = stream_import_outages(db, b"[]")

        assert result["imported"] == 0
        assert result["failed_count"] == 0
        assert result["total_rows"] == 0


class TestRowValidation:
    def test_bad_detected_at_is_reported_with_its_field_and_row(self, db):
        result = stream_import_outages(db, _body(_row(), _row(id="outage-002", detected_at="yesterday")))

        assert result["failed_count"] == 1
        failure = result["failed_rows"][0]
        assert failure["row"] == 1
        assert failure["id"] == "outage-002"
        assert failure["status"] == "error"
        assert failure["errors"][0]["field"] == "detected_at"

    def test_naive_timestamp_is_rejected(self, db):
        """A naive datetime silently becomes "local" elsewhere; the schema says no."""
        result = stream_import_outages(db, _body(_row(detected_at="2026-01-01T00:00:00")))

        assert result["failed_count"] == 1
        assert result["failed_rows"][0]["errors"][0]["field"] == "detected_at"

    def test_missing_required_field_is_reported(self, db):
        row = _row()
        del row["description"]

        result = stream_import_outages(db, _body(row))

        assert result["failed_count"] == 1
        assert result["failed_rows"][0]["errors"][0]["field"] == "description"

    def test_bad_enum_value_is_reported(self, db):
        result = stream_import_outages(db, _body(_row(severity="catastrophic-but-not-in-the-enum")))

        assert result["failed_count"] == 1
        assert result["failed_rows"][0]["errors"][0]["field"] == "severity"

    def test_row_that_is_not_an_object_is_reported(self, db):
        result = stream_import_outages(db, _body(_row(), "not-an-object"))

        assert result["failed_count"] == 1
        assert result["failed_rows"][0]["row"] == 1
        assert result["failed_rows"][0]["errors"][0]["type"] == "TypeError"

    def test_every_bad_field_of_a_row_is_listed(self, db):
        result = stream_import_outages(db, _body(_row(detected_at="nope", severity="nope")))

        fields = {error["field"] for error in result["failed_rows"][0]["errors"]}
        assert fields == {"detected_at", "severity"}

    def test_row_numbers_survive_chunking(self, db):
        """The reported row is the row's position, not the chunk's start (#547)."""
        rows = [_row(id=f"outage-{index:03d}") for index in range(250)]
        rows[123] = _row(id="outage-123", detected_at="not-a-date")

        result = stream_import_outages(db, _body(*rows), chunk_size=50, consistency=ImportConsistency.partial)

        assert result["failed_count"] == 1
        assert result["failed_rows"][0]["row"] == 123
        assert result["failed_rows"][0]["id"] == "outage-123"


class TestRejectOrNothing:
    def test_one_bad_row_withholds_the_whole_batch_by_default(self, db):
        result = stream_import_outages(db, _body(_row(), _row(id="outage-002", detected_at="nope")))

        assert result["consistency"] == ImportConsistency.atomic.value
        assert result["imported"] == 0
        assert result["valid_row_ids"] == []
        assert result["failed_count"] == 1

    def test_partial_mode_accepts_the_valid_subset_explicitly(self, db):
        result = stream_import_outages(
            db,
            _body(_row(), _row(id="outage-002", detected_at="nope"), _row(id="outage-003")),
            consistency=ImportConsistency.partial,
        )

        assert result["imported"] == 2
        assert result["valid_row_ids"] == ["outage-001", "outage-003"]
        assert result["failed_count"] == 1

    def test_partial_mode_still_reports_every_failure(self, db):
        result = stream_import_outages(
            db,
            _body(_row(detected_at="nope"), _row(id="outage-002", severity="nope")),
            consistency=ImportConsistency.partial,
        )

        assert result["imported"] == 0
        assert result["failed_count"] == 2
        assert [failure["row"] for failure in result["failed_rows"]] == [0, 1]


class TestMalformedBodies:
    def test_invalid_json_reports_an_error_with_the_same_keys(self, db):
        result = stream_import_outages(db, b"{not json")

        assert result["error"] == "invalid json"
        assert result["imported"] == 0
        assert result["failed_count"] == 0
        assert result["valid_row_ids"] == []

    def test_non_list_body_is_rejected(self, db):
        result = stream_import_outages(db, b'{"id": "outage-001"}')

        assert result["error"] == "expected a list"
        assert result["imported"] == 0


class TestRowCap:
    def test_rows_past_the_cap_are_reported_not_silently_dropped(self, db):
        rows = [_row(id=f"outage-{index:03d}") for index in range(5)]

        result = stream_import_outages(db, _body(*rows), max_rows=3)

        assert result["total_rows"] == 5
        assert result["truncated"] == 2
        assert result["imported"] == 3

    def test_no_truncation_is_reported_as_zero(self, db):
        result = stream_import_outages(db, _body(_row()), max_rows=10)

        assert result["truncated"] == 0
