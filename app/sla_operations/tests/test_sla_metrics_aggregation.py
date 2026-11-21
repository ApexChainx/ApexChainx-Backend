from types import SimpleNamespace

from app.sla_operations.services.sla_metrics_aggregation_kpis import (
    SlaKpiAggregate,
    aggregate_sla_metrics,
    aggregate_sla_metrics_as_dict,
)


def test_aggregate_sla_metrics_empty_and_none_inputs():
    assert aggregate_sla_metrics(None) == SlaKpiAggregate(0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert aggregate_sla_metrics([]) == SlaKpiAggregate(0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_aggregate_sla_metrics_primary_behavior_with_mappings_and_objects():
    records = [
        {"status": "met", "mttr_minutes": 10, "amount": 100, "payment_type": "reward"},
        SimpleNamespace(status="violated", mttr_minutes=30, amount=-50, payment_type="penalty"),
        {"status": "met", "mttr_minutes": 20, "amount": 25},
    ]
    agg = aggregate_sla_metrics(records)
    assert agg.total_records == 3
    assert agg.met_count == 2
    assert agg.violated_count == 1
    assert agg.unknown_status_count == 0
    assert agg.violation_rate == round(1 / 3, 6)
    assert agg.avg_mttr_minutes == 20.0
    assert agg.total_reward_amount == 125.0
    assert agg.total_penalty_amount == 50.0
    assert agg.net_payout_amount == 75.0


def test_aggregate_sla_metrics_handles_malformed_edge_cases_without_exceptions():
    records = [
        {"status": "unknown", "mttr_minutes": "not-a-number", "amount": "NaN-ish", "payment_type": "reward"},
        {"status": "MET", "mttr_minutes": "15.5", "amount": "12.5", "payment_type": "reward"},
        {"status": "violated", "mttr_minutes": -5, "amount": 7, "payment_type": "penalty"},
        {"status": None, "mttr_minutes": None, "amount": None},
        123,  # unsupported record shape should be ignored safely
        "bad",
    ]
    agg = aggregate_sla_metrics(records)
    assert agg.total_records == 4
    assert agg.met_count == 1
    assert agg.violated_count == 1
    assert agg.unknown_status_count == 2
    assert agg.violation_rate == 0.5
    assert agg.avg_mttr_minutes == 15.5
    assert agg.total_reward_amount == 12.5
    assert agg.total_penalty_amount == 7.0
    assert agg.net_payout_amount == 5.5


def test_aggregate_sla_metrics_as_dict_matches_model():
    records = [{"status": "met", "mttr_minutes": 5, "amount": 10, "payment_type": "reward"}]
    agg = aggregate_sla_metrics(records)
    as_dict = aggregate_sla_metrics_as_dict(records)
    assert as_dict["total_records"] == agg.total_records
    assert as_dict["met_count"] == agg.met_count
    assert as_dict["net_payout_amount"] == agg.net_payout_amount
    assert set(as_dict.keys()) == {
        "total_records",
        "met_count",
        "violated_count",
        "unknown_status_count",
        "violation_rate",
        "avg_mttr_minutes",
        "total_reward_amount",
        "total_penalty_amount",
        "net_payout_amount",
    }
