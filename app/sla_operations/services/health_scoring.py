"""Operational health scoring across SLA, outage, settlement, and delivery domains.

Each domain is reduced to a success component and an optional latency component.
Domains with no measured operations report ``no_data`` and are excluded from the
aggregate by default. All arithmetic is bounded and deterministic: counters are
clamped to their declared totals, malformed values are noted as indicators, and
the final score is rounded to two decimals.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

HEALTHY_THRESHOLD = 90.0
DEGRADED_THRESHOLD = 70.0
SUCCESS_COMPONENT_WEIGHT = 0.7
LATENCY_COMPONENT_WEIGHT = 0.3
HIGH_FAILURE_RATE = 0.1
DEGRADED_OPERATIONS_RATE = 0.2
LATENCY_PRESSURE_RATIO = 0.8


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    NO_DATA = "no_data"


@dataclass(frozen=True)
class DomainMetrics:
    domain: str
    total: int = 0
    failed: int = 0
    degraded: int = 0
    latency_ms: float | None = None
    latency_budget_ms: float | None = None
    weight: float = 1.0


@dataclass(frozen=True)
class HealthIndicator:
    code: str
    domain: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "domain": self.domain, "detail": self.detail}


@dataclass(frozen=True)
class DomainHealth:
    domain: str
    score: float
    status: HealthStatus
    success_rate: float
    latency_ratio: float | None
    weight: float
    no_data: bool
    indicators: tuple[HealthIndicator, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "score": self.score,
            "status": self.status.value,
            "success_rate": self.success_rate,
            "latency_ratio": self.latency_ratio,
            "weight": self.weight,
            "no_data": self.no_data,
            "indicators": [indicator.to_dict() for indicator in self.indicators],
        }


@dataclass(frozen=True)
class HealthReport:
    score: float
    status: HealthStatus
    domains: tuple[DomainHealth, ...]
    indicators: tuple[HealthIndicator, ...]
    data_coverage: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "status": self.status.value,
            "domains": [domain.to_dict() for domain in self.domains],
            "indicators": [indicator.to_dict() for indicator in self.indicators],
            "data_coverage": self.data_coverage,
        }


def _read(value: Any, names: Iterable[str], default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _status_for_score(score: float) -> HealthStatus:
    if score >= HEALTHY_THRESHOLD:
        return HealthStatus.HEALTHY
    if score >= DEGRADED_THRESHOLD:
        return HealthStatus.DEGRADED
    return HealthStatus.CRITICAL


def coerce_domain_metrics(value: Any) -> tuple[DomainMetrics, tuple[HealthIndicator, ...]]:
    """Normalize a domain snapshot, clamping invalid fields and recording notes."""
    notes: list[HealthIndicator] = []
    raw_domain = _read(value, ("domain", "name", "component"))
    domain = str(raw_domain).strip() if raw_domain is not None else ""
    if not domain or domain.lower() in {"none", "unknown"}:
        domain = "unknown"
        notes.append(HealthIndicator("invalid_domain", domain, "domain label defaulted to 'unknown'"))

    raw_total_value = _read(value, ("total", "total_operations", "count"), 0)
    raw_total = _safe_int(raw_total_value)
    if raw_total is None or raw_total < 0:
        total = 0
        notes.append(HealthIndicator("invalid_total", domain, "operation count defaulted to zero"))
    else:
        total = raw_total

    def bounded_count(names: Iterable[str], label: str) -> int:
        raw = _safe_int(_read(value, names, 0))
        if raw is None or raw < 0:
            notes.append(HealthIndicator(f"invalid_{label}", domain, f"{label} count defaulted to zero"))
            return 0
        return min(raw, total)

    failed = bounded_count(("failed", "failures", "errors"), "failed")
    degraded = bounded_count(("degraded", "degraded_operations"), "degraded")

    raw_latency_value = _read(value, ("latency_ms", "latency"))
    raw_latency = _safe_float(raw_latency_value)
    if raw_latency_value is not None and (raw_latency is None or raw_latency < 0):
        raw_latency = None
        notes.append(HealthIndicator("invalid_latency", domain, "latency ignored"))

    raw_budget_value = _read(value, ("latency_budget_ms", "latency_budget"))
    raw_budget = _safe_float(raw_budget_value)
    if raw_budget_value is not None and (raw_budget is None or raw_budget <= 0):
        raw_budget = None
        notes.append(HealthIndicator("invalid_latency_budget", domain, "latency budget ignored"))

    raw_weight_value = _read(value, ("weight",), 1.0)
    raw_weight = _safe_float(raw_weight_value)
    if raw_weight is None or raw_weight <= 0:
        raw_weight = 1.0
        notes.append(HealthIndicator("invalid_weight", domain, "weight defaulted to 1.0"))

    metrics = DomainMetrics(
        domain=domain,
        total=total,
        failed=failed,
        degraded=degraded,
        latency_ms=raw_latency,
        latency_budget_ms=raw_budget,
        weight=raw_weight,
    )
    return metrics, tuple(notes)


def _combine_metrics(left: DomainMetrics, right: DomainMetrics) -> DomainMetrics:
    latency_ms = left.latency_ms
    if right.latency_ms is not None:
        latency_ms = right.latency_ms if latency_ms is None else max(latency_ms, right.latency_ms)

    latency_budget_ms = left.latency_budget_ms
    if right.latency_budget_ms is not None:
        latency_budget_ms = right.latency_budget_ms if latency_budget_ms is None else min(
            latency_budget_ms, right.latency_budget_ms
        )

    return DomainMetrics(
        domain=left.domain,
        total=left.total + right.total,
        failed=left.failed + right.failed,
        degraded=left.degraded + right.degraded,
        latency_ms=latency_ms,
        latency_budget_ms=latency_budget_ms,
        weight=max(left.weight, right.weight),
    )


def compute_domain_health(metrics: Any) -> DomainHealth:
    """Compute one domain's bounded score, status, and explanatory indicators."""
    normalized, notes = coerce_domain_metrics(metrics)
    indicators = list(notes)

    if normalized.total <= 0:
        indicators.append(HealthIndicator("no_data", normalized.domain, "no operations measured"))
        return DomainHealth(
            domain=normalized.domain,
            score=0.0,
            status=HealthStatus.NO_DATA,
            success_rate=0.0,
            latency_ratio=None,
            weight=normalized.weight,
            no_data=True,
            indicators=tuple(indicators),
        )

    success_rate = (normalized.total - normalized.failed - 0.5 * normalized.degraded) / normalized.total
    success_rate = min(1.0, max(0.0, success_rate))

    latency_ratio: float | None = None
    if normalized.latency_ms is not None and normalized.latency_budget_ms is not None:
        over_budget = max(0.0, normalized.latency_ms - normalized.latency_budget_ms)
        latency_ratio = min(1.0, max(0.0, 1.0 - over_budget / normalized.latency_budget_ms))

    if latency_ratio is None:
        raw_score = success_rate
    else:
        raw_score = SUCCESS_COMPONENT_WEIGHT * success_rate + LATENCY_COMPONENT_WEIGHT * latency_ratio
    score = round(100.0 * raw_score, 2)

    if normalized.failed / normalized.total >= HIGH_FAILURE_RATE:
        indicators.append(HealthIndicator("high_failure_rate", normalized.domain, "failure rate is elevated"))
    if normalized.degraded / normalized.total >= DEGRADED_OPERATIONS_RATE:
        indicators.append(HealthIndicator("degraded_operations", normalized.domain, "degraded operations are elevated"))
    if latency_ratio is not None and latency_ratio < LATENCY_PRESSURE_RATIO:
        indicators.append(HealthIndicator("latency_budget_pressure", normalized.domain, "latency exceeds 80% of budget"))

    return DomainHealth(
        domain=normalized.domain,
        score=score,
        status=_status_for_score(score),
        success_rate=round(success_rate, 4),
        latency_ratio=round(latency_ratio, 4) if latency_ratio is not None else None,
        weight=normalized.weight,
        no_data=False,
        indicators=tuple(indicators),
    )


def _dedupe_indicators(items: Iterable[HealthIndicator]) -> tuple[HealthIndicator, ...]:
    seen: set[tuple[str, str, str]] = set()
    result: list[HealthIndicator] = []
    for item in items:
        key = (item.code, item.domain or "", item.detail)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def compute_operational_health(domains: Iterable[Any], *, include_no_data: bool = False) -> HealthReport:
    """Aggregate domain snapshots into a weighted operational health report.

    Repeated domain labels are combined by summing counters, taking the worst
    latency, the tightest budget, and the largest weight. ``data_coverage`` is
    the share of total weight represented by domains included in the score.
    """
    aggregates: dict[str, DomainMetrics] = {}
    notes: list[HealthIndicator] = []
    domain_count = 0

    for raw_domain in domains:
        domain_count += 1
        metrics, domain_notes = coerce_domain_metrics(raw_domain)
        notes.extend(domain_notes)
        if metrics.domain in aggregates:
            aggregates[metrics.domain] = _combine_metrics(aggregates[metrics.domain], metrics)
        else:
            aggregates[metrics.domain] = metrics

    domain_health = tuple(compute_domain_health(aggregates[name]) for name in sorted(aggregates))
    for domain in domain_health:
        notes.extend(domain.indicators)

    if domain_count == 0:
        notes.append(HealthIndicator("no_domains", None, "no domain metrics supplied"))

    total_weight = sum(domain.weight for domain in domain_health)
    included = tuple(domain for domain in domain_health if include_no_data or not domain.no_data)
    included_weight = sum(domain.weight for domain in included)

    if total_weight <= 0 or included_weight <= 0:
        score = 0.0
        status = HealthStatus.NO_DATA
        coverage = 0.0
        notes.append(HealthIndicator("no_scored_domains", None, "no domain data available for scoring"))
    else:
        score = round(sum(domain.score * domain.weight for domain in included) / included_weight, 2)
        status = _status_for_score(score)
        coverage = round(included_weight / total_weight, 4)

    return HealthReport(
        score=score,
        status=status,
        domains=domain_health,
        indicators=_dedupe_indicators(notes),
        data_coverage=coverage,
    )


score_operational_health = compute_operational_health
