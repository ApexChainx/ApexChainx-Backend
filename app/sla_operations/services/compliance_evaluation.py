"""Deterministic SLA compliance evaluation against severity thresholds."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

_UsableThreshold = tuple[float | None, float | None]


class ComplianceThreshold(BaseModel):
    """Per-severity limits; either check may be omitted."""

    max_mttr_minutes: float | None = None
    min_availability_percent: float | None = None


class ComplianceObservation(BaseModel):
    """Outcome input; MTTR may be supplied directly or derived from timestamps."""

    outage_id: str = ""
    severity: str | None = None
    mttr_minutes: float | None = None
    detected_at: datetime | None = None
    resolved_at: datetime | None = None
    availability_percent: float | None = None


class ComplianceEvaluation(BaseModel):
    outage_id: str
    severity: str
    status: Literal["compliant", "non_compliant", "unevaluated"]
    mttr_minutes: float | None = None
    threshold_minutes: float | None = None
    availability_percent: float | None = None
    availability_threshold_percent: float | None = None
    reasons: list[str]


class SeverityCompliance(BaseModel):
    severity: str
    evaluated_records: int
    compliant_records: int
    non_compliant_records: int
    unevaluated_records: int
    compliance_rate_percent: float | None = None


class ComplianceReport(BaseModel):
    total_records: int
    evaluated_records: int
    compliant_records: int
    non_compliant_records: int
    unevaluated_records: int
    compliance_rate_percent: float | None = None
    invalid_threshold_severities: list[str]
    by_severity: list[SeverityCompliance]
    evaluations: list[ComplianceEvaluation]


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


def _rate(numerator: int, denominator: int, precision: int) -> float | None:
    if denominator <= 0:
        return None
    return _round(100.0 * numerator / denominator, precision)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _extract_mttr(observation: ComplianceObservation) -> tuple[float | None, str | None]:
    explicit = _finite_float(observation.mttr_minutes)
    if explicit is not None:
        if explicit < 0:
            return None, "negative_mttr"
        return explicit, None

    start = _as_utc(observation.detected_at)
    end = _as_utc(observation.resolved_at)
    if start is not None and end is not None:
        duration = (end - start).total_seconds() / 60.0
        if duration < 0:
            return None, "negative_duration"
        return duration, None
    return None, "missing_mttr"


def _coerce_observation(item: object) -> tuple[ComplianceObservation, str | None]:
    try:
        if isinstance(item, ComplianceObservation):
            return item, None
        if isinstance(item, Mapping):
            return ComplianceObservation.model_validate(dict(item)), None
        return ComplianceObservation.model_validate(item), None
    except (ValidationError, TypeError, ValueError):
        return ComplianceObservation(), "invalid_record"


def _coerce_thresholds(
    thresholds: object,
) -> tuple[dict[str, _UsableThreshold], set[str]]:
    usable: dict[str, _UsableThreshold] = {}
    invalid: set[str] = set()
    if not isinstance(thresholds, Mapping):
        return usable, invalid

    for raw_severity in sorted(thresholds, key=lambda key: str(key)):
        severity = _normalize_text(raw_severity)
        raw_threshold = thresholds[raw_severity]
        try:
            if isinstance(raw_threshold, ComplianceThreshold):
                threshold = raw_threshold
            elif isinstance(raw_threshold, Mapping):
                threshold = ComplianceThreshold.model_validate(dict(raw_threshold))
            else:
                threshold = ComplianceThreshold.model_validate(raw_threshold)
        except (ValidationError, TypeError, ValueError):
            usable.pop(severity, None)
            invalid.add(severity)
            continue

        max_mttr = _finite_float(threshold.max_mttr_minutes)
        min_availability = _finite_float(threshold.min_availability_percent)
        invalid_values = (
            (max_mttr is not None and max_mttr < 0)
            or (min_availability is not None and not 0.0 <= min_availability <= 100.0)
            or (max_mttr is None and min_availability is None)
        )
        if invalid_values:
            usable.pop(severity, None)
            invalid.add(severity)
            continue
        usable[severity] = (max_mttr, min_availability)

    return usable, invalid


def _evaluate_observation(
    observation: ComplianceObservation,
    usable_thresholds: dict[str, _UsableThreshold],
    invalid_thresholds: set[str],
    precision: int,
    coerce_reason: str | None = None,
) -> tuple[ComplianceEvaluation, str]:
    severity = _normalize_text(observation.severity)
    reasons: list[str] = []
    if coerce_reason is not None:
        evaluation = ComplianceEvaluation(
            outage_id=observation.outage_id,
            severity=severity,
            status="unevaluated",
            reasons=[coerce_reason],
        )
        return evaluation, severity

    mttr, mttr_reason = _extract_mttr(observation)
    availability = _finite_float(observation.availability_percent)
    threshold = usable_thresholds.get(severity)
    max_mttr: float | None = None
    min_availability: float | None = None

    passes = 0
    failures = 0
    if threshold is None:
        reasons.append("invalid_threshold" if severity in invalid_thresholds else "missing_threshold")
    else:
        max_mttr, min_availability = threshold
        if max_mttr is not None:
            if mttr is None:
                reasons.append(mttr_reason or "missing_mttr")
            elif mttr > max_mttr:
                failures += 1
                reasons.append("mttr_exceeded_threshold")
            else:
                passes += 1
                reasons.append("mttr_within_threshold")

        if min_availability is not None:
            if availability is None or not 0.0 <= availability <= 100.0:
                reasons.append("invalid_availability")
            elif availability < min_availability:
                failures += 1
                reasons.append("availability_below_threshold")
            else:
                passes += 1
                reasons.append("availability_within_threshold")

    status: Literal["compliant", "non_compliant", "unevaluated"]
    if failures:
        status = "non_compliant"
    elif passes:
        status = "compliant"
    else:
        status = "unevaluated"

    evaluation = ComplianceEvaluation(
        outage_id=observation.outage_id,
        severity=severity,
        status=status,
        mttr_minutes=_round(mttr, precision) if mttr is not None else None,
        threshold_minutes=_round(max_mttr, precision) if max_mttr is not None else None,
        availability_percent=_round(availability, precision) if availability is not None else None,
        availability_threshold_percent=_round(min_availability, precision) if min_availability is not None else None,
        reasons=reasons,
    )
    return evaluation, severity


def evaluate_sla_compliance(
    observations: Iterable[ComplianceObservation | Mapping[str, object]],
    thresholds: Mapping[str, ComplianceThreshold | Mapping[str, object]] | None,
    *,
    precision: int = 2,
) -> ComplianceReport:
    """Evaluate each observation; exact threshold values are compliant."""
    normalized_precision = _normalize_precision(precision)
    usable_thresholds, invalid_thresholds = _coerce_thresholds(thresholds)

    evaluations: list[ComplianceEvaluation] = []
    severity_counts: dict[str, dict[str, int]] = {}

    def ensure_severity(severity: str) -> dict[str, int]:
        return severity_counts.setdefault(
            severity,
            {
                "evaluated_records": 0,
                "compliant_records": 0,
                "non_compliant_records": 0,
                "unevaluated_records": 0,
            },
        )

    for severity in sorted(set(usable_thresholds) | invalid_thresholds):
        ensure_severity(severity)

    total_records = 0
    for item in observations:
        total_records += 1
        observation, coerce_reason = _coerce_observation(item)
        evaluation, severity = _evaluate_observation(
            observation,
            usable_thresholds,
            invalid_thresholds,
            normalized_precision,
            coerce_reason,
        )
        evaluations.append(evaluation)
        counts = ensure_severity(severity)
        if evaluation.status != "unevaluated":
            counts["evaluated_records"] += 1
        if evaluation.status == "compliant":
            counts["compliant_records"] += 1
        elif evaluation.status == "non_compliant":
            counts["non_compliant_records"] += 1
        else:
            counts["unevaluated_records"] += 1

    compliant_records = sum(counts["compliant_records"] for counts in severity_counts.values())
    non_compliant_records = sum(counts["non_compliant_records"] for counts in severity_counts.values())
    unevaluated_records = sum(counts["unevaluated_records"] for counts in severity_counts.values())
    evaluated_records = compliant_records + non_compliant_records

    by_severity = [
        SeverityCompliance(
            severity=severity,
            evaluated_records=counts["evaluated_records"],
            compliant_records=counts["compliant_records"],
            non_compliant_records=counts["non_compliant_records"],
            unevaluated_records=counts["unevaluated_records"],
            compliance_rate_percent=_rate(
                counts["compliant_records"], counts["evaluated_records"], normalized_precision
            ),
        )
        for severity, counts in sorted(severity_counts.items())
    ]

    return ComplianceReport(
        total_records=total_records,
        evaluated_records=evaluated_records,
        compliant_records=compliant_records,
        non_compliant_records=non_compliant_records,
        unevaluated_records=unevaluated_records,
        compliance_rate_percent=_rate(compliant_records, evaluated_records, normalized_precision),
        invalid_threshold_severities=sorted(invalid_thresholds),
        by_severity=by_severity,
        evaluations=evaluations,
    )


class ComplianceEvaluator:
    """Service facade for SLA compliance evaluation."""

    @staticmethod
    def evaluate(
        observations: Iterable[ComplianceObservation | Mapping[str, object]],
        thresholds: Mapping[str, ComplianceThreshold | Mapping[str, object]] | None,
        *,
        precision: int = 2,
    ) -> ComplianceReport:
        return evaluate_sla_compliance(observations, thresholds, precision=precision)


__all__ = [
    "ComplianceEvaluation",
    "ComplianceEvaluator",
    "ComplianceObservation",
    "ComplianceReport",
    "ComplianceThreshold",
    "SeverityCompliance",
    "evaluate_sla_compliance",
]
