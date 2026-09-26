"""Tests for SLACalculationResult and SLACalculationError — Issue #94.

Validates that compute_device_sla returns typed Pydantic models rather
than loose dicts, and that OpenAPI can reflect the new shape.
"""

import csv
import io

import pytest

from app.models.sla import SLACalculationError, SLACalculationResult
from app.utils.analytics_exporter import export_analytics_summary


class TestSLACalculationResult:
    def test_valid_no_outages(tself):
        """Model accepts a result with zero outages (happy path)."""
        result = SLACalculationResult(
            device_id="dev-1",
            period="2025-03",
            period_start="2025-03-01T00:00:00",
            period_end="2025-04-01T00:00:00",
            total_outages=0,
            violated_outages=0,
            avg_mttr_minutes=0.0,
            availability_percentage=100.0,
            is_violated=False,
            sla_thresholds={"availability": 99.9, "mttr": 60.0},
            violation_reasons=[],
        )
        assert result.device_id == "dev-1"
        assert result.is_violated is False
        assert result.availability_percentage == 100.0

    def test_valid_with_violation(self):
        """Model accepts a result with violations."""
        result = SLACalculationResult(
            device_id="dev-2",
            period="2025-Q2",
            period_start="2025-04-01T00:00:00",
            period_end="2025-07-01T00:00:00",
            total_outages=3,
            violated_outages=2,
            avg_mttr_minutes=120.0,
            availability_percentage=98.5,
            is_violated=True,
            sla_thresholds={"availability": 99.9, "mttr": 60.0},
            violation_reasons=[
                "Availability 98.5% below threshold 99.9%",
                "MTTR 120.0 minutes above threshold 60.0 minutes",
            ],
            outage_details=[
                {
                    "id": "out-1",
                    "site_id": "site-1",
                    "site_name": "Site A",
                    "started_at": "2025-04-01T08:00:00",
                    "resolved_at": "2025-04-01T10:00:00",
                    "severity": "high",
                }
            ],
        )
        assert result.is_violated is True
        assert len(result.violation_reasons) == 2
        assert len(result.outage_details) == 1

    def test_field_constraints(self):
        """availability_percentage must be 0-100, counts must be >=0."""
        with pytest.raises(ValueError):
            SLACalculationResult(
                device_id="d",
                period="2025-01",
                period_start="2025-01-01T00:00:00",
                period_end="2025-02-01T00:00:00",
                total_outages=-1,
                violated_outages=0,
                avg_mttr_minutes=0.0,
                availability_percentage=100.0,
                is_violated=False,
                sla_thresholds={},
                violation_reasons=[],
            )

        with pytest.raises(ValueError):
            SLACalculationResult(
                device_id="d",
                period="2025-01",
                period_start="2025-01-01T00:00:00",
                period_end="2025-02-01T00:00:00",
                total_outages=0,
                violated_outages=0,
                avg_mttr_minutes=0.0,
                availability_percentage=150.0,
                is_violated=False,
                sla_thresholds={},
                violation_reasons=[],
            )

    def test_json_schema_reflects_shape(self):
        """Verify the model produces a predictable JSON schema for OpenAPI."""
        schema = SLACalculationResult.model_json_schema()
        assert schema["type"] == "object"
        assert "device_id" in schema["properties"]
        assert "avg_mttr_minutes" in schema["properties"]
        assert schema["properties"]["availability_percentage"]["minimum"] == 0.0
        assert schema["properties"]["availability_percentage"]["maximum"] == 100.0


class TestSLACalculationError:
    def test_error_model(self):
        error = SLACalculationError(
            device_id="dev-err",
            period="2025-03",
            error_code="COMPUTE_FAILED",
            detail="Database connection lost",
        )
        assert error.error_code == "COMPUTE_FAILED"
        assert error.detail == "Database connection lost"


class TestAnalyticsSummaryCSV:
    """CSV export must be RFC 4180-compliant (single header, uniform rows)."""
    def _make_summary(self, include_trends: bool) -> dict:
        summary = {
            "kpi": {
                "total_outages": 1,
                "availability": 99.9,
                "violations": 0,
                "rewards": 1,
                "penalties": 0,
            },
            "trends": [],
            "trend_count": 0,
        }
        if include_trends:
            summary["trends"] = [
                {
                    "date": "2025-03-01",
                    "total_outages": 0,
                    "violations": 0,
                    "rewards": 0,
                    "penalties": 0,
                }
            ]
            summary["trend_count"] = 1
        return summary

    def _parse(self, csv_output) -> list[list[str]]:
        if isinstance(csv_output, bytes):
            csv_output = csv_output.decode()
        return list(csv.reader(io.StringIO(csv_output)))

    def test_populated_summary_uniform_columns(self):
        summary = self._make_summary(include_trends=True)
        csv_output = export_analytics_summary(summary, format="csv")
        rows = self._parse(csv_output)

        assert rows, "CSV should have at least a header row"
        column_counts = {len(row) for row in rows}
        assert len(column_counts) == 1, (
            f"Rows have inconsistent column counts: {sorted(column_counts)}"
        )
        assert not any(row and row[0].startswith("#") for row in rows), (
            "Found comment/non-standard line"
        )
        assert not any(not row for row in rows), "Found blank line"

    def test_empty_trends_same_schema(self):
        summary_with = self._make_summary(include_trends=True)
        summary_empty = self._make_summary(include_trends=False)

        csv_with = self._parse(export_analytics_summary(summary_with, format="csv"))
        csv_empty = self._parse(export_analytics_summary(summary_empty, format="csv"))

        assert len({len(row) for row in csv_with}) == 1
        assert len({len(row) for row in csv_empty}) == 1

        # Both CSVs must expose the exact same schema (header row) — no hardcoded fallback.
        assert csv_with[0] == csv_empty[0], (
            "Empty dataset must use the same header as populated dataset"
        )
