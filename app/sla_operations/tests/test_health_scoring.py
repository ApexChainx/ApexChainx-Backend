from app.sla_operations.services.health_scoring import (
    HealthStatus,
    coerce_domain_metrics,
    compute_domain_health,
    compute_operational_health,
    score_operational_health,
)


def test_healthy_domain_with_latency_within_budget():
    health = compute_domain_health(
        {
            "domain": "sla",
            "total": 100,
            "failed": 0,
            "degraded": 0,
            "latency_ms": 50,
            "latency_budget_ms": 100,
        }
    )

    assert health.score == 100.0
    assert health.status is HealthStatus.HEALTHY
    assert health.latency_ratio == 1.0
    assert health.indicators == ()
    assert health.to_dict()["status"] == "healthy"


def test_success_component_only_when_latency_is_absent():
    health = compute_domain_health({"domain": "settlement", "total": 10, "failed": 1})

    assert health.score == 90.0
    assert health.status is HealthStatus.HEALTHY
    assert health.latency_ratio is None
    assert health.no_data is False


def test_failures_degraded_operations_and_latency_lower_the_score():
    health = compute_domain_health(
        {
            "domain": "outages",
            "total": 10,
            "failed": 2,
            "degraded": 2,
            "latency_ms": 180,
            "latency_budget_ms": 100,
        }
    )

    assert health.score == 55.0
    assert health.status is HealthStatus.CRITICAL
    assert health.success_rate == 0.7
    assert health.latency_ratio == 0.2
    assert {indicator.code for indicator in health.indicators} == {
        "high_failure_rate",
        "degraded_operations",
        "latency_budget_pressure",
    }


def test_empty_input_returns_no_data_report():
    report = compute_operational_health([])

    assert report.score == 0.0
    assert report.status is HealthStatus.NO_DATA
    assert report.domains == ()
    assert report.data_coverage == 0.0
    assert {indicator.code for indicator in report.indicators} == {"no_domains", "no_scored_domains"}


def test_invalid_fields_are_clamped_and_reported():
    metrics, notes = coerce_domain_metrics(
        {
            "domain": "webhooks",
            "total": 10,
            "failed": 99,
            "degraded": -5,
            "latency_ms": -1,
            "latency_budget_ms": 0,
            "weight": 0,
        }
    )

    assert metrics.failed == 10
    assert metrics.degraded == 0
    assert metrics.latency_ms is None
    assert metrics.latency_budget_ms is None
    assert metrics.weight == 1.0
    assert {note.code for note in notes} == {
        "invalid_degraded",
        "invalid_latency",
        "invalid_latency_budget",
        "invalid_weight",
    }

    health = compute_domain_health(metrics)
    assert health.score == 0.0
    assert health.status is HealthStatus.CRITICAL


def test_duplicate_domain_labels_are_aggregated_deterministically():
    report = compute_operational_health(
        [
            {"domain": "sla", "total": 10, "failed": 0, "weight": 1},
            {"domain": "sla", "total": 10, "failed": 2, "weight": 2},
        ]
    )

    assert len(report.domains) == 1
    assert report.domains[0].domain == "sla"
    assert report.domains[0].score == 90.0
    assert report.domains[0].weight == 2.0
    assert "high_failure_rate" in {indicator.code for indicator in report.indicators}


def test_no_data_domains_are_excluded_unless_requested():
    domains = [
        {"domain": "sla", "total": 10, "failed": 0, "weight": 1},
        {"domain": "settlement", "total": 0, "failed": 0, "weight": 1},
    ]

    default_report = compute_operational_health(domains)
    assert default_report.score == 100.0
    assert default_report.data_coverage == 0.5

    included_report = score_operational_health(domains, include_no_data=True)
    assert included_report.score == 50.0
    assert included_report.status is HealthStatus.CRITICAL
    assert included_report.data_coverage == 1.0


def test_report_to_dict_is_json_shaped():
    report = compute_operational_health([{"domain": "sla", "total": 4, "failed": 0}])

    payload = report.to_dict()

    assert set(payload) == {"score", "status", "domains", "indicators", "data_coverage"}
    assert set(payload["domains"][0]) == {
        "domain",
        "score",
        "status",
        "success_rate",
        "latency_ratio",
        "weight",
        "no_data",
        "indicators",
    }
    assert payload["indicators"] == []
