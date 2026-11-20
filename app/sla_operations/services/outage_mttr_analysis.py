"""Deterministic MTTR distribution and trend analysis for outage records."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

DEFAULT_GRANULARITY = "day"
VALID_GRANULARITIES: frozenset[str] = frozenset({"day", "week", "month"})
MTTR_BANDS: tuple[tuple[str, float | None], ...] = (
    ("<=15", 15.0),
    ("<=30", 30.0),
    ("<=60", 60.0),
    ("<=120", 120.0),
    (">120", None),
)
_RESOLVED_STATUSES = frozenset({"resolved", "closed", "met", "violated"})


class OutageMTTRRecord(BaseModel):
    """Outage input; MTTR may be supplied directly or derived from timestamps."""

    outage_id: str = ""
    severity: str | None = None
    status: str | None = None
    detected_at: datetime | None = None
    resolved_at: datetime | None = None
    mttr_minutes: float | None = None


class MTTRSummary(BaseModel):
    """Distribution statistics for a set of MTTR samples."""

    count: int = 0
    mean_minutes: float = 0.0
    median_minutes: float = 0.0
    min_minutes: float = 0.0
    max_minutes: float = 0.0
    p90_minutes: float = 0.0
    p95_minutes: float = 0.0


class MTTRBandCount(BaseModel):
    label: str
    count: int


class MTTRTrendPoint(BaseModel):
    """One ordered time bucket; change is relative to the previous bucket mean."""

    period: str
    outage_count: int
    mean_minutes: float
    median_minutes: float
    max_minutes: float
    change_from_previous_mean_minutes: float | None = None


class SeverityMTTRBreakdown(BaseModel):
    severity: str
    summary: MTTRSummary


class OutageMTTRAnalysis(BaseModel):
    total_records: int
    analysed_records: int
    excluded_records: int
    unresolved_records: int
    exclusion_reasons: dict[str, int]
    granularity: Literal["day", "week", "month"]
    precision: int
    summary: MTTRSummary
    distribution: list[MTTRBandCount]
    by_severity: list[SeverityMTTRBreakdown]
    trend: list[MTTRTrendPoint]


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _normalize_text(value: object, default: str = "unknown") -> str:
    if value is None:
        return default
    text = str(value).strip().lower()
    return text or default


def _normalize_precision(value: object) -> int:
    number = _finite_float(value)
    if number is None:
        return 2
    return max(0, min(6, int(number)))


def _round(value: float, precision: int) -> float:
    return round(float(value), precision)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _extract_mttr(record: OutageMTTRRecord) -> tuple[float | None, str | None]:
    explicit = _finite_float(record.mttr_minutes)
    if explicit is not None:
        if explicit < 0:
            return None, "negative_mttr"
        return explicit, None

    start = _as_utc(record.detected_at)
    end = _as_utc(record.resolved_at)
    if start is not None and end is not None:
        duration = (end - start).total_seconds() / 60.0
        if duration < 0:
            return None, "negative_duration"
        return duration, None
    return None, "missing_mttr"


def _coerce_record(item: object) -> tuple[OutageMTTRRecord, str | None]:
    try:
        if isinstance(item, OutageMTTRRecord):
            return item, None
        if isinstance(item, Mapping):
            return OutageMTTRRecord.model_validate(dict(item)), None
        return OutageMTTRRecord.model_validate(item), None
    except (ValidationError, TypeError, ValueError):
        return OutageMTTRRecord(), "invalid_record"


def _period_key(moment: datetime | None, granularity: str) -> str | None:
    value = _as_utc(moment)
    if value is None:
        return None
    if granularity == "week":
        iso = value.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"
    if granularity == "month":
        return f"{value.year:04d}-{value.month:02d}"
    return value.date().isoformat()


def _percentile(ordered_values: list[float], percent: float) -> float:
    if not ordered_values:
        return 0.0
    rank = math.ceil((percent / 100.0) * len(ordered_values))
    index = min(len(ordered_values) - 1, max(0, rank - 1))
    return ordered_values[index]


def _summarize(values: list[float], precision: int) -> MTTRSummary:
    if not values:
        return MTTRSummary()
    ordered = sorted(values)
    return MTTRSummary(
        count=len(ordered),
        mean_minutes=_round(sum(ordered) / len(ordered), precision),
        median_minutes=_round(float(statistics.median(ordered)), precision),
        min_minutes=_round(ordered[0], precision),
        max_minutes=_round(ordered[-1], precision),
        p90_minutes=_round(_percentile(ordered, 90.0), precision),
        p95_minutes=_round(_percentile(ordered, 95.0), precision),
    )


def _band_label(value: float) -> str:
    for label, upper_bound in MTTR_BANDS:
        if upper_bound is None or value <= upper_bound:
            return label
    return MTTR_BANDS[-1][0]


def _distribution(values: list[float]) -> list[MTTRBandCount]:
    counts = {label: 0 for label, _ in MTTR_BANDS}
    for value in values:
        counts[_band_label(value)] += 1
    return [MTTRBandCount(label=label, count=counts[label]) for label, _ in MTTR_BANDS]


def analyze_outage_mttr(
    records: Iterable[OutageMTTRRecord | Mapping[str, object]],
    *,
    granularity: str = DEFAULT_GRANULARITY,
    precision: int = 2,
) -> OutageMTTRAnalysis:
    """Analyze MTTR samples; naive timestamps are treated as UTC and invalid samples are excluded."""
    normalized_granularity = _normalize_text(granularity, DEFAULT_GRANULARITY)
    if normalized_granularity not in VALID_GRANULARITIES:
        normalized_granularity = DEFAULT_GRANULARITY
    normalized_precision = _normalize_precision(precision)

    values: list[float] = []
    severity_values: dict[str, list[float]] = {}
    trend_series: dict[str, list[float]] = {}
    exclusion_reasons: dict[str, int] = {}
    total_records = 0
    excluded_records = 0
    unresolved_records = 0

    for item in records:
        total_records += 1
        record, coerce_reason = _coerce_record(item)
        status = _normalize_text(record.status, "")
        if coerce_reason is None and record.resolved_at is None and status not in _RESOLVED_STATUSES:
            unresolved_records += 1

        mttr, reason = _extract_mttr(record)
        if coerce_reason is not None:
            reason = coerce_reason

        if reason is not None or mttr is None:
            failure = reason or "invalid_mttr"
            exclusion_reasons[failure] = exclusion_reasons.get(failure, 0) + 1
            excluded_records += 1
            continue

        value = mttr
        values.append(value)

        severity = _normalize_text(record.severity)
        severity_values.setdefault(severity, []).append(value)

        moment = record.detected_at if record.detected_at is not None else record.resolved_at
        period = _period_key(moment, normalized_granularity)
        if period is not None:
            trend_series.setdefault(period, []).append(value)

    by_severity = [
        SeverityMTTRBreakdown(severity=severity, summary=_summarize(severity_values[severity], normalized_precision))
        for severity in sorted(severity_values)
    ]

    trend: list[MTTRTrendPoint] = []
    previous_mean: float | None = None
    for period in sorted(trend_series):
        summary = _summarize(trend_series[period], normalized_precision)
        change = None if previous_mean is None else summary.mean_minutes - previous_mean
        trend.append(
            MTTRTrendPoint(
                period=period,
                outage_count=summary.count,
                mean_minutes=summary.mean_minutes,
                median_minutes=summary.median_minutes,
                max_minutes=summary.max_minutes,
                change_from_previous_mean_minutes=_round(change, normalized_precision)
                if change is not None
                else None,
            )
        )
        previous_mean = summary.mean_minutes

    return OutageMTTRAnalysis(
        total_records=total_records,
        analysed_records=len(values),
        excluded_records=excluded_records,
        unresolved_records=unresolved_records,
        exclusion_reasons=dict(sorted(exclusion_reasons.items())),
        granularity=normalized_granularity,
        precision=normalized_precision,
        summary=_summarize(values, normalized_precision),
        distribution=_distribution(values),
        by_severity=by_severity,
        trend=trend,
    )


class OutageMTTRAnalyzer:
    """Service facade for outage MTTR analysis."""

    @staticmethod
    def analyze(
        records: Iterable[OutageMTTRRecord | Mapping[str, object]],
        *,
        granularity: str = DEFAULT_GRANULARITY,
        precision: int = 2,
    ) -> OutageMTTRAnalysis:
        return analyze_outage_mttr(records, granularity=granularity, precision=precision)


__all__ = [
    "DEFAULT_GRANULARITY",
    "MTTR_BANDS",
    "MTTRBandCount",
    "MTTRSummary",
    "MTTRTrendPoint",
    "OutageMTTRAnalysis",
    "OutageMTTRAnalyzer",
    "OutageMTTRRecord",
    "SeverityMTTRBreakdown",
    "analyze_outage_mttr",
]
