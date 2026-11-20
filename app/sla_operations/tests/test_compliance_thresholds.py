"""Tests for the compliance-threshold workspace schemas (#853)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.compliance_thresholds import (
    ComplianceEntityType,
    ComplianceEvaluationResult,
    ComplianceStatus,
    SeverityLevel,
    ThresholdComparison,
    ThresholdDefinition,
    ThresholdMetric,
    ThresholdOperator,
    ThresholdSource,
    meets_threshold,
)

NOW = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)


def threshold_payload(**overrides):
    payload = {
        "metric": ThresholdMetric.MTTR_MINUTES,
        "operator": ThresholdOperator.LTE,
        "value": 60.0,
    }
    payload.update(overrides)
    return payload


def comparison_payload(**overrides):
    payload = {
        "threshold": threshold_payload(),
        "observed_value": 45.0,
        "meets_threshold": True,
    }
    payload.update(overrides)
    return payload


def evaluation_payload(**overrides):
    payload = {
        "entity_id": "device-1",
        "entity_type": ComplianceEntityType.DEVICE,
        "status": ComplianceStatus.COMPLIANT,
        "score": 100.0,
        "comparisons": [comparison_payload()],
        "evaluated_at": NOW,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "operator,observed,limit,expected",
    [
        (ThresholdOperator.GTE, 60.0, 60.0, True),
        (ThresholdOperator.GTE, 59.9, 60.0, False),
        (ThresholdOperator.GT, 60.0, 60.0, False),
        (ThresholdOperator.GT, 60.1, 60.0, True),
        (ThresholdOperator.LTE, 60.0, 60.0, True),
        (ThresholdOperator.LTE, 60.1, 60.0, False),
        (ThresholdOperator.LT, 60.0, 60.0, False),
        (ThresholdOperator.LT, 59.9, 60.0, True),
        (ThresholdOperator.EQ, 60.0, 60.0, True),
        (ThresholdOperator.EQ, 60.1, 60.0, False),
    ],
)
def test_meets_threshold_covers_operator_boundaries(operator, observed, limit, expected):
    assert meets_threshold(operator, observed, limit) is expected


def test_definition_accepts_representative_payload():
    definition = ThresholdDefinition.model_validate(
        threshold_payload(
            metric="availability",
            operator="gte",
            value=99.9,
            severity="critical",
            source="contract",
            policy_version="2",
            description="Contractual availability floor",
        )
    )

    assert definition.metric is ThresholdMetric.AVAILABILITY
    assert definition.severity is SeverityLevel.CRITICAL
    assert definition.source is ThresholdSource.CONTRACT


@pytest.mark.parametrize(
    "overrides",
    [
        {"metric": ThresholdMetric.AVAILABILITY, "value": 0.0},
        {"metric": ThresholdMetric.AVAILABILITY, "value": 100.1},
        {"metric": ThresholdMetric.VIOLATION_RATE, "value": 1.1},
        {"metric": ThresholdMetric.VIOLATION_RATE, "value": -0.1},
        {"metric": ThresholdMetric.MTTR_MINUTES, "value": -1.0},
        {"policy_version": "0"},
        {"policy_version": "v1"},
        {"operator": "between"},
        {"unexpected": True},
    ],
)
def test_definition_rejects_out_of_range_or_invalid_payloads(overrides):
    with pytest.raises(ValidationError):
        ThresholdDefinition.model_validate(threshold_payload(**overrides))


def test_comparison_derives_delta_and_accepts_aware_timestamp():
    comparison = ThresholdComparison.model_validate(comparison_payload(evaluated_at=NOW, site_id="site-1"))

    assert comparison.delta == pytest.approx(-15.0)
    assert comparison.evaluated_at == NOW


@pytest.mark.parametrize(
    "overrides",
    [
        {"meets_threshold": False},
        {"evaluated_at": NOW.replace(tzinfo=None)},
        {"site_id": ""},
        {"threshold": threshold_payload(operator="unsupported")},
    ],
)
def test_comparison_rejects_inconsistent_payloads(overrides):
    with pytest.raises(ValidationError):
        ThresholdComparison.model_validate(comparison_payload(**overrides))


def test_evaluation_derives_violated_metrics():
    violating = comparison_payload(
        threshold=threshold_payload(metric=ThresholdMetric.AVAILABILITY, operator=ThresholdOperator.GTE, value=99.9),
        observed_value=99.5,
        meets_threshold=False,
    )
    evaluation = ComplianceEvaluationResult.model_validate(
        evaluation_payload(
            status=ComplianceStatus.NON_COMPLIANT,
            score=75.0,
            comparisons=[comparison_payload(), violating],
        )
    )

    assert evaluation.violated_metrics == [ThresholdMetric.AVAILABILITY]


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "comparisons": [
                comparison_payload(
                    threshold=threshold_payload(
                        metric=ThresholdMetric.AVAILABILITY, operator=ThresholdOperator.GTE, value=99.9
                    ),
                    observed_value=99.5,
                    meets_threshold=False,
                )
            ]
        },
        {
            "comparisons": [comparison_payload(), comparison_payload()],
            "violated_metrics": [ThresholdMetric.MTTR_MINUTES],
        },
        {
            "comparisons": [comparison_payload(), comparison_payload(threshold=threshold_payload(policy_version="2"))]
        },
        {"comparisons": []},
        {"score": -0.1},
        {"score": 100.1},
        {"evaluated_at": NOW.replace(tzinfo=None)},
    ],
)
def test_evaluation_rejects_inconsistent_payloads(overrides):
    with pytest.raises(ValidationError):
        ComplianceEvaluationResult.model_validate(evaluation_payload(**overrides))


def test_enum_values_are_machine_readable():
    assert ThresholdMetric.MTTR_MINUTES.value == "mttr_minutes"
    assert ThresholdOperator.GTE.value == "gte"
    assert ThresholdSource.POLICY.value == "policy"
    assert ComplianceEntityType.SLA_RESULT.value == "sla_result"
    assert ComplianceStatus.NON_COMPLIANT.value == "non_compliant"
