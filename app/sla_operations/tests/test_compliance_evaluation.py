from datetime import UTC, datetime, timedelta

from app.sla_operations.services.compliance_evaluation import (
    ComplianceEvaluator,
    ComplianceThreshold,
    evaluate_sla_compliance,
)


def test_evaluate_sla_compliance_uses_inclusive_boundaries_and_aggregates():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    thresholds = {
        "critical": ComplianceThreshold(max_mttr_minutes=30.0, min_availability_percent=99.9),
        "high": {"max_mttr_minutes": 60.0},
        "low": {"min_availability_percent": 99.0},
    }
    observations = [
        {"outage_id": "boundary", "severity": "CRITICAL", "mttr_minutes": 30.0, "availability_percent": 99.9},
        {"outage_id": "slow", "severity": "critical", "mttr_minutes": 45.0, "availability_percent": 99.95},
        {"outage_id": "low-availability", "severity": "critical", "mttr_minutes": 10.0, "availability_percent": 99.5},
        {
            "outage_id": "derived",
            "severity": "high",
            "detected_at": start,
            "resolved_at": start + timedelta(minutes=60),
        },
        {"outage_id": "no-threshold", "severity": "unknown", "mttr_minutes": 5.0},
        {"outage_id": "missing-availability", "severity": "critical", "mttr_minutes": 5.0},
    ]

    report = evaluate_sla_compliance(observations, thresholds)

    by_id = {evaluation.outage_id: evaluation for evaluation in report.evaluations}
    assert by_id["boundary"].status == "compliant"
    assert by_id["boundary"].reasons == [
        "mttr_within_threshold",
        "availability_within_threshold",
    ]
    assert by_id["slow"].status == "non_compliant"
    assert by_id["slow"].reasons == ["mttr_exceeded_threshold", "availability_within_threshold"]
    assert by_id["low-availability"].status == "non_compliant"
    assert by_id["missing-availability"].status == "compliant"
    assert "invalid_availability" in by_id["missing-availability"].reasons
    assert by_id["derived"].mttr_minutes == 60.0
    assert by_id["derived"].status == "compliant"
    assert by_id["no-threshold"].status == "unevaluated"
    assert by_id["no-threshold"].reasons == ["missing_threshold"]

    assert report.total_records == 6
    assert report.evaluated_records == 5
    assert report.compliant_records == 3
    assert report.non_compliant_records == 2
    assert report.unevaluated_records == 1
    assert report.compliance_rate_percent == 60.0
    assert report.invalid_threshold_severities == []
    assert [(item.severity, item.compliance_rate_percent) for item in report.by_severity] == [
        ("critical", 50.0),
        ("high", 100.0),
        ("low", None),
        ("unknown", None),
    ]


def test_evaluate_sla_compliance_ignores_invalid_thresholds_and_records():
    thresholds = {
        "bad": {"max_mttr_minutes": -1.0},
        "empty": {},
        "availability-only": {"min_availability_percent": 0.0},
    }
    observations = [
        {"outage_id": "bad-threshold", "severity": "bad", "mttr_minutes": 5.0},
        {"outage_id": "empty-threshold", "severity": "empty", "mttr_minutes": 5.0},
        {"outage_id": "valid", "severity": "availability-only", "availability_percent": 0.0},
        object(),
    ]

    report = evaluate_sla_compliance(observations, thresholds)

    by_id = {evaluation.outage_id: evaluation for evaluation in report.evaluations}
    assert by_id["bad-threshold"].reasons == ["invalid_threshold"]
    assert by_id["empty-threshold"].reasons == ["invalid_threshold"]
    assert by_id["valid"].status == "compliant"
    assert by_id[""].reasons == ["invalid_record"]
    assert report.invalid_threshold_severities == ["bad", "empty"]
    assert report.evaluated_records == 1
    assert report.compliance_rate_percent == 100.0


def test_evaluate_sla_compliance_handles_empty_inputs():
    report = ComplianceEvaluator.evaluate([], None)

    assert report.total_records == 0
    assert report.evaluated_records == 0
    assert report.compliance_rate_percent is None
    assert report.evaluations == []
    assert report.by_severity == []
    assert report.invalid_threshold_severities == []
