"""Summarize SLA dispute states and resolution indicators (#869).

The service works on plain mappings and does not import ORM models or touch a
database session. Status labels are trimmed and lower-cased; blank or unknown
labels land in an ``unknown`` bucket. Canonical statuses are always present in
the output, with any extra labels appended in sorted order for determinism.

Resolution indicators count the presence of resolver, notes, timestamps and
result references, plus terminal-state completeness and resolution durations
derived from ``flagged_at``/``resolved_at``. Ratios are rounded to four
decimals and are 0.0 for an empty dataset.

Complexity: O(n + k) for n disputes and k distinct status labels.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

DEFAULT_STATUSES: tuple[str, ...] = ("pending", "resolved", "rejected", "unknown")
TERMINAL_STATUSES: frozenset[str] = frozenset({"resolved", "rejected"})
UNKNOWN = "unknown"
RATIO_DIGITS = 4


class FindingCode(str, Enum):
    invalid_row = "invalid_row"
    invalid_timestamp = "invalid_timestamp"
    invalid_resolution_window = "invalid_resolution_window"


class DisputeFinding(BaseModel):
    """A non-fatal issue found while summarizing an input row."""

    source_index: int
    code: FindingCode
    message: str


class ResolutionIndicators(BaseModel):
    """Presence and completeness indicators for dispute resolution metadata."""

    with_resolved_at: int = 0
    with_resolved_by: int = 0
    with_resolution_notes: int = 0
    with_proposed_result: int = 0
    with_baseline_result: int = 0
    terminal_complete: int = 0
    terminal_incomplete: int = 0
    pending_with_resolution_metadata: int = 0
    average_resolution_minutes: float | None = None
    min_resolution_minutes: float | None = None
    max_resolution_minutes: float | None = None


class DisputeSummaryReport(BaseModel):
    """Aggregate dispute summary for one dataset."""

    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    status_ratios: dict[str, float] = Field(default_factory=dict)
    resolution_rate: float = 0.0
    rejection_rate: float = 0.0
    pending_rate: float = 0.0
    indicators: ResolutionIndicators = Field(default_factory=ResolutionIndicators)
    findings: list[DisputeFinding] = Field(default_factory=list)


def _normalize(value: object) -> str:
    if isinstance(value, str):
        text = value.strip().lower()
        if text:
            return text
    return UNKNOWN


def _ratio(count: int, total: int) -> float:
    return round(count / total, RATIO_DIGITS) if total else 0.0


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_reference(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _rows(disputes: Iterable[object] | None) -> Iterable[object]:
    if disputes is None:
        return ()
    try:
        return iter(disputes)
    except TypeError:
        return ()


def summarize_disputes(
    disputes: Iterable[object] | None,
    *,
    statuses: Iterable[str] = DEFAULT_STATUSES,
) -> DisputeSummaryReport:
    """Summarize dispute states and resolution indicators.

    Args:
        disputes: Iterable of dispute-like mappings. ``None`` is treated as
            empty; non-mappings are counted as unknown with a finding.
        statuses: Extra status labels to include even when absent from data.

    Returns:
        A report with state counts/ratios, resolution indicators, and findings.
        Missing optional metadata is never an error; only invalid timestamps,
        invalid resolution windows, and non-mapping rows produce findings.
    """
    status_counts: dict[str, int] = {}
    findings: list[DisputeFinding] = []
    indicators = ResolutionIndicators()
    durations: list[float] = []
    total = 0

    for index, raw_dispute in enumerate(_rows(disputes)):
        total += 1
        if not isinstance(raw_dispute, Mapping):
            status = UNKNOWN
            findings.append(
                DisputeFinding(
                    source_index=index,
                    code=FindingCode.invalid_row,
                    message=f"expected a mapping, got {type(raw_dispute).__name__}",
                )
            )
        else:
            status = _normalize(raw_dispute.get("status"))

            parsed: dict[str, datetime | None] = {}
            for field in ("flagged_at", "resolved_at"):
                raw_value = raw_dispute.get(field)
                parsed[field] = _parse_timestamp(raw_value)
                if raw_value is not None and parsed[field] is None:
                    findings.append(
                        DisputeFinding(
                            source_index=index,
                            code=FindingCode.invalid_timestamp,
                            message=f"{field} must be a timezone-aware timestamp",
                        )
                    )

            has_resolved_at = parsed["resolved_at"] is not None
            has_resolved_by = _has_text(raw_dispute.get("resolved_by"))
            has_notes = _has_text(raw_dispute.get("resolution_notes"))

            if has_resolved_at:
                indicators.with_resolved_at += 1
            if has_resolved_by:
                indicators.with_resolved_by += 1
            if has_notes:
                indicators.with_resolution_notes += 1
            if _has_reference(raw_dispute.get("proposed_sla_result_id")):
                indicators.with_proposed_result += 1
            if _has_reference(raw_dispute.get("baseline_sla_result_id")):
                indicators.with_baseline_result += 1

            flagged_at = parsed["flagged_at"]
            resolved_at = parsed["resolved_at"]
            if flagged_at is not None and resolved_at is not None:
                if resolved_at >= flagged_at:
                    durations.append(round((resolved_at - flagged_at).total_seconds() / 60, 2))
                else:
                    findings.append(
                        DisputeFinding(
                            source_index=index,
                            code=FindingCode.invalid_resolution_window,
                            message="resolved_at precedes flagged_at",
                        )
                    )

            if status in TERMINAL_STATUSES:
                if has_resolved_at and has_resolved_by and has_notes:
                    indicators.terminal_complete += 1
                else:
                    indicators.terminal_incomplete += 1
            if status == "pending" and (has_resolved_at or has_resolved_by or has_notes):
                indicators.pending_with_resolution_metadata += 1

        status_counts[status] = status_counts.get(status, 0) + 1

    ordered_statuses: list[str] = []
    for value in (*DEFAULT_STATUSES, *statuses, *sorted(status_counts)):
        key = _normalize(value)
        if key not in ordered_statuses:
            ordered_statuses.append(key)

    if durations:
        indicators.average_resolution_minutes = round(sum(durations) / len(durations), 2)
        indicators.min_resolution_minutes = min(durations)
        indicators.max_resolution_minutes = max(durations)

    return DisputeSummaryReport(
        total=total,
        by_status={status: status_counts.get(status, 0) for status in ordered_statuses},
        status_ratios={status: _ratio(status_counts.get(status, 0), total) for status in ordered_statuses},
        resolution_rate=_ratio(status_counts.get("resolved", 0), total),
        rejection_rate=_ratio(status_counts.get("rejected", 0), total),
        pending_rate=_ratio(status_counts.get("pending", 0), total),
        indicators=indicators,
        findings=findings,
    )
