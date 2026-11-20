from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.sla_operations.services.sla_metrics_aggregation import (
    SLAMetricsAggregationService,
    aggregate_sla_metrics,
)


@dataclass
class OutageMetric:
    status: str
    mttr_minutes: float
    amount: float


@pytest.fixture
def service() -> SLAMetricsAggregationService:
    return SLAMetricsAggregationService()


def test_aggregate_empty_dataset_returns_zero_metrics(service: SLAMetricsAggregationService) -> None:
    result = service.aggregate([])

    assert result.to_dict() == {
        "total_outages": 0,
        "violation_rate": 0.0,
        "avg_mttr": 0.0,
        "payout_sum": 0.0,
    }


def test_aggregate_mixed_records(service: SLAMetricsAggregationService) -> None:
    records = [
        {"status": "violated", "mttr_minutes": 90, "amount": 125.555},
        {"status": "compliant", "mttr_minutes": 30, "amount": 0},
        OutageMetric(status="breached", mttr_minutes=60.5, amount=-20),
    ]

    result = service.aggregate(records)

    assert result.total_outages == 3
    assert result.violation_rate == pytest.approx(0.6667)
    assert result.avg_mttr == pytest.approx(60.17)
    assert result.payout_sum == pytest.approx(105.56)


def test_aggregate_all_compliant_records(service: SLAMetricsAggregationService) -> None:
    records = [
        {"status": "compliant", "mttr_minutes": 10, "amount": 0},
        {"status": "paid", "mttr_minutes": 20, "amount": 50},
    ]

    result = aggregate_sla_metrics(records)

    assert result.total_outages == 2
    assert result.violation_rate == 0.0
    assert result.avg_mttr == 15.0
    assert result.payout_sum == 50.0


def test_aggregate_supports_mapping_access_and_pydantic_style_dump(
    service: SLAMetricsAggregationService,
) -> None:
    result = service.aggregate_metrics(
        [{"status": "violation", "mttr_minutes": 5, "amount": 12.25}]
    )

    assert result["total_outages"] == 1
    assert result.model_dump()["payout_sum"] == 12.25


@pytest.mark.parametrize("records", [None, "not-records", {"status": "compliant"}])
def test_aggregate_rejects_invalid_top_level_inputs(
    service: SLAMetricsAggregationService,
    records: object,
) -> None:
    with pytest.raises(TypeError):
        service.aggregate(records)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"status": "compliant", "amount": 10}, "mttr_minutes"),
        ({"status": "compliant", "mttr_minutes": -1, "amount": 10}, "negative mttr_minutes"),
        ({"status": "compliant", "mttr_minutes": True, "amount": 10}, "invalid mttr_minutes"),
        ({"status": "compliant", "mttr_minutes": 1, "amount": float("inf")}, "non-finite amount"),
        ({"status": "", "mttr_minutes": 1, "amount": 10}, "invalid status"),
    ],
)
def test_aggregate_rejects_malformed_records(
    service: SLAMetricsAggregationService,
    record: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        service.aggregate([record])
