"""Micro-benchmarks for top 5 hot service functions (pytest-benchmark).

These benchmarks track performance regressions in hot-path functions.
Run: pytest tests/benchmarks/ --benchmark-only
Acceptance: <30s nightly, >1.2x median fails (regression alert).
"""

import pytest

from app.services.metrics import MetricsRegistry
from app.services.sla.config import SLA_CONFIG
from app.services.sla.sla_calculator import SLACalculator

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def metrics_registry() -> MetricsRegistry:
    """Fresh metrics registry with pre-populated data for benchmarking."""
    registry = MetricsRegistry()
    # Pre-populate with realistic data
    for i in range(100):
        registry.increment_counter("requests_total", tags={"method": "GET", "status": "200"})
        registry.set_gauge("active_connections", float(i % 10))
        registry.record_histogram("request_duration_ms", float(i * 1.5))
        registry.record_timer("db_query_ms", float(i * 0.5))
    return registry


@pytest.fixture
def sla_calculator() -> SLACalculator:
    return SLACalculator()


# ── Benchmark 1: SLA calculator (hottest path) ──────────────────────────────


def test_benchmark_sla_calculator_violated(benchmark, sla_calculator):
    """Benchmark SLA calculation for a violated SLA (critical severity)."""
    result = benchmark(
        sla_calculator.calculate,
        outage_id="OUT-001",
        severity="critical",
        mttr_minutes=45,
        started_at="2024-01-01T00:00:00Z",
        resolved_at="2024-01-01T00:45:00Z",
    )
    assert result.status == "violated"


def test_benchmark_sla_calculator_met(benchmark, sla_calculator):
    """Benchmark SLA calculation for a met SLA (high severity)."""
    result = benchmark(
        sla_calculator.calculate,
        outage_id="OUT-002",
        severity="high",
        mttr_minutes=10,
        started_at="2024-01-01T00:00:00Z",
        resolved_at="2024-01-01T00:10:00Z",
    )
    assert result.status == "met"


def test_benchmark_sla_calculator_exceptional(benchmark, sla_calculator):
    """Benchmark SLA calculation for exceptional performance (<50% threshold)."""
    result = benchmark(
        sla_calculator.calculate,
        outage_id="OUT-003",
        severity="medium",
        mttr_minutes=5,
        started_at="2024-01-01T00:00:00Z",
        resolved_at="2024-01-01T00:05:00Z",
    )
    assert result.rating == "exceptional"


# ── Benchmark 2: Metrics registry (hot path for observability) ──────────────


def test_benchmark_increment_counter(benchmark, metrics_registry):
    """Benchmark counter increment (frequent hot-path operation)."""
    benchmark(metrics_registry.increment_counter, "requests_total", tags={"method": "GET"})


def test_benchmark_set_gauge(benchmark, metrics_registry):
    """Benchmark gauge setting (used for connection pool tracking)."""
    benchmark(metrics_registry.set_gauge, "active_connections", 42.0)


def test_benchmark_record_histogram(benchmark, metrics_registry):
    """Benchmark histogram recording (latency tracking)."""
    benchmark(metrics_registry.record_histogram, "request_duration_ms", 15.5)


def test_benchmark_record_timer(benchmark, metrics_registry):
    """Benchmark timer recording (DB query timing)."""
    benchmark(metrics_registry.record_timer, "db_query_ms", 3.2)


def test_benchmark_metrics_summary(benchmark, metrics_registry):
    """Benchmark full metrics summary generation (Prometheus scrape path)."""
    summary = benchmark(metrics_registry.get_metrics_summary)
    assert "counters" in summary
    assert "gauges" in summary
    assert "histograms" in summary
    assert "timers" in summary


# ── Benchmark 3: SLA config hash computation ─────────────────────────────────


def test_benchmark_sla_config_hash(benchmark):
    """Benchmark SLA config content hash computation (integrity verification)."""
    from app.services.sla.config import _compute_content_hash

    result = benchmark(
        _compute_content_hash,
        "critical",
        SLA_CONFIG["critical"],
        1,
    )
    assert len(result) == 64  # SHA-256 hex digest


# ── Benchmark 4: Webhook header building ─────────────────────────────────────


def test_benchmark_webhook_header_building(benchmark):
    """Benchmark webhook header generation (includes SHA-256 signing)."""
    from unittest.mock import MagicMock

    from app.services.webhook_service import _build_headers

    webhook = MagicMock()
    webhook.secret = "test-secret-key-for-benchmarking"
    webhook.id = "wh-bench-001"

    payload = '{"event": "sla_violation", "data": {"device_id": "dev-001"}}'

    headers = benchmark(_build_headers, webhook, payload)
    assert "X-Webhook-Signature" in headers
    assert "traceparent" in headers


# ── Benchmark 5: Period parsing ──────────────────────────────────────────────


def test_benchmark_period_parsing_monthly(benchmark):
    """Benchmark period parsing for monthly SLA computation."""
    from unittest.mock import MagicMock

    from app.services.sla_service import SLAOrchestrator

    orchestrator = SLAOrchestrator(MagicMock())
    start, end = benchmark(orchestrator.parse_period, "2024-06")
    assert start.month == 6
    assert end.month == 7


def test_benchmark_period_parsing_quarterly(benchmark):
    """Benchmark period parsing for quarterly SLA computation."""
    from unittest.mock import MagicMock

    from app.services.sla_service import SLAOrchestrator

    orchestrator = SLAOrchestrator(MagicMock())
    start, end = benchmark(orchestrator.parse_period, "2024-Q3")
    assert start.month == 7
    assert end.month == 10
