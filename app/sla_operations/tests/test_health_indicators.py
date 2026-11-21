from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.health_indicators import (
    AggregatedOperationalHealthResponse,
    DomainHealthIndicator,
    HealthDomain,
    HealthState,
    OperationalHealthSummary,
)


def test_domain_health_indicator_valid_payload():
    indicator = DomainHealthIndicator(
        domain=HealthDomain.PAYMENT,
        state=HealthState.DEGRADED,
        summary="Retry backlog above threshold",
        observed_at=datetime(2026, 11, 20, 13, 15, tzinfo=UTC),
        metric_value=42.0,
        metric_unit="pending_payments",
        details={"pending": 42},
    )
    assert indicator.domain == HealthDomain.PAYMENT
    assert indicator.state == HealthState.DEGRADED
    assert indicator.metric_value == 42.0


def test_domain_health_indicator_rejects_blank_summary():
    with pytest.raises(ValidationError):
        DomainHealthIndicator(domain=HealthDomain.SLA, state=HealthState.HEALTHY, summary="   ")


def test_aggregated_operational_health_response_valid_and_consistent():
    indicators = [
        DomainHealthIndicator(domain=HealthDomain.SLA, state=HealthState.HEALTHY, summary="SLA ok"),
        DomainHealthIndicator(domain=HealthDomain.OUTAGE, state=HealthState.HEALTHY, summary="Outage ok"),
        DomainHealthIndicator(
            domain=HealthDomain.PAYMENT,
            state=HealthState.DEGRADED,
            summary="Payment backlog",
            metric_value=5,
            metric_unit="pending",
        ),
    ]
    response = AggregatedOperationalHealthResponse(
        overall_state=HealthState.DEGRADED,
        generated_at=datetime(2026, 11, 20, 13, 16, tzinfo=UTC),
        indicators=indicators,
        summary=OperationalHealthSummary(
            total_indicators=3, healthy_count=2, degraded_count=1, unhealthy_count=0
        ),
    )
    assert response.overall_state == HealthState.DEGRADED
    assert response.summary.unhealthy_count == 0


def test_aggregated_operational_health_rejects_inconsistent_overall_state():
    indicators = [
        DomainHealthIndicator(domain=HealthDomain.WEBHOOK, state=HealthState.UNHEALTHY, summary="Webhook failures")
    ]
    with pytest.raises(ValidationError):
        AggregatedOperationalHealthResponse(
            overall_state=HealthState.HEALTHY,
            indicators=indicators,
            summary=OperationalHealthSummary(
                total_indicators=1, healthy_count=0, degraded_count=0, unhealthy_count=1
            ),
        )


def test_aggregated_operational_health_rejects_invalid_summary_counts():
    indicators = [DomainHealthIndicator(domain=HealthDomain.SLA, state=HealthState.HEALTHY, summary="ok")]
    with pytest.raises(ValidationError):
        AggregatedOperationalHealthResponse(
            overall_state=HealthState.HEALTHY,
            indicators=indicators,
            summary=OperationalHealthSummary(
                total_indicators=2, healthy_count=1, degraded_count=0, unhealthy_count=0
            ),
        )


def test_domain_health_indicator_rejects_invalid_enum_and_extra_fields():
    with pytest.raises(ValidationError):
        DomainHealthIndicator.model_validate(
            {"domain": "unknown", "state": "healthy", "summary": "ok"}
        )
    with pytest.raises(ValidationError):
        DomainHealthIndicator.model_validate(
            {"domain": "sla", "state": "healthy", "summary": "ok", "extra_field": True}
        )
