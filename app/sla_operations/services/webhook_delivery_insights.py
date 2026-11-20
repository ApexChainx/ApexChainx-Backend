"""Deterministic webhook delivery outcome and retry-signal insights."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime

from pydantic import BaseModel, ValidationError

_STATUS_ALIASES = {
    "success": "success",
    "succeeded": "success",
    "delivered": "success",
    "ok": "success",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "retrying": "retrying",
    "retry": "retrying",
    "pending": "pending",
    "queued": "pending",
    "sending": "sending",
    "in_flight": "sending",
    "dead_letter": "dead_letter",
    "dead-letter": "dead_letter",
    "deadletter": "dead_letter",
    "breaker_open": "breaker_open",
    "breakeropen": "breaker_open",
    "circuit_open": "breaker_open",
}
_STATUS_FIELDS = {
    "success": "success",
    "failed": "failed",
    "retrying": "retrying",
    "pending": "pending",
    "sending": "sending",
    "dead_letter": "dead_letter",
    "breaker_open": "breaker_open",
    "unknown": "unknown",
}


class WebhookDeliveryRecord(BaseModel):
    """Delivery input; next_retry_at and dead_lettered_at are retry lifecycle signals."""

    delivery_id: str = ""
    webhook_id: str | None = None
    event: str | None = None
    status: str | None = None
    attempt_count: float | None = None
    next_retry_at: datetime | None = None
    delivered_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    response_status_code: float | None = None
    error_message: str | None = None


class DeliveryTotals(BaseModel):
    total: int = 0
    success: int = 0
    failed: int = 0
    retrying: int = 0
    pending: int = 0
    sending: int = 0
    dead_letter: int = 0
    breaker_open: int = 0
    unknown: int = 0
    attempted_records: int = 0
    average_attempts: float = 0.0
    max_attempts: int = 0


class RetrySignals(BaseModel):
    records_with_attempts: int
    retried_records: int
    retries_scheduled: int
    at_or_above_attempt_threshold: int
    dead_lettered: int
    breaker_open: int
    success_responses: int
    client_error_responses: int
    server_error_responses: int
    error_message_present: int


class DeliveryGroup(BaseModel):
    name: str
    totals: DeliveryTotals
    success_rate_percent: float
    retry_rate_percent: float


class WebhookDeliveryInsights(BaseModel):
    total_records: int
    invalid_records: int
    totals: DeliveryTotals
    success_rate_percent: float
    failure_rate_percent: float
    retrying_rate_percent: float
    dead_letter_rate_percent: float
    retry_rate_percent: float
    retry_attempt_threshold: int
    retry_signals: RetrySignals
    by_event: list[DeliveryGroup]
    by_webhook: list[DeliveryGroup]


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


def _normalize_threshold(value: object) -> int:
    number = _finite_float(value)
    if number is None:
        return 3
    return max(1, int(number))


def _round(value: float, precision: int) -> float:
    return round(float(value), precision)


def _percentage(numerator: int, denominator: int, precision: int) -> float:
    if denominator <= 0:
        return 0.0
    return _round(100.0 * numerator / denominator, precision)


def _normalize_status(value: object) -> str:
    text = _normalize_text(value, "unknown")
    return _STATUS_ALIASES.get(text, text)


def _attempt_count(value: object) -> int:
    number = _finite_float(value)
    if number is None or number <= 0:
        return 0
    return int(number)


def _coerce_record(item: object) -> tuple[WebhookDeliveryRecord, str | None]:
    try:
        if isinstance(item, WebhookDeliveryRecord):
            return item, None
        if isinstance(item, Mapping):
            return WebhookDeliveryRecord.model_validate(dict(item)), None
        return WebhookDeliveryRecord.model_validate(item), None
    except (ValidationError, TypeError, ValueError):
        return WebhookDeliveryRecord(), "invalid_record"


def _new_totals() -> DeliveryTotals:
    return DeliveryTotals()


def _add_to_totals(totals: DeliveryTotals, status: str, attempts: int) -> None:
    totals.total += 1
    field = _STATUS_FIELDS.get(status, "unknown")
    setattr(totals, field, getattr(totals, field) + 1)
    if attempts > 0:
        totals.attempted_records += 1
    totals.max_attempts = max(totals.max_attempts, attempts)


def _finalize_totals(totals: DeliveryTotals, attempt_sum: int, precision: int) -> DeliveryTotals:
    average = attempt_sum / totals.total if totals.total else 0.0
    return DeliveryTotals(
        total=totals.total,
        success=totals.success,
        failed=totals.failed,
        retrying=totals.retrying,
        pending=totals.pending,
        sending=totals.sending,
        dead_letter=totals.dead_letter,
        breaker_open=totals.breaker_open,
        unknown=totals.unknown,
        attempted_records=totals.attempted_records,
        average_attempts=_round(average, precision),
        max_attempts=totals.max_attempts,
    )


def _build_groups(
    totals: Mapping[str, DeliveryTotals],
    attempt_sums: Mapping[str, int],
    retried_counts: Mapping[str, int],
    precision: int,
) -> list[DeliveryGroup]:
    groups: list[DeliveryGroup] = []
    for name in sorted(totals):
        group_totals = _finalize_totals(totals[name], attempt_sums.get(name, 0), precision)
        groups.append(
            DeliveryGroup(
                name=name,
                totals=group_totals,
                success_rate_percent=_percentage(group_totals.success, group_totals.total, precision),
                retry_rate_percent=_percentage(retried_counts.get(name, 0), group_totals.total, precision),
            )
        )
    return groups


def summarize_webhook_deliveries(
    records: Iterable[WebhookDeliveryRecord | Mapping[str, object]],
    *,
    retry_attempt_threshold: int = 3,
    precision: int = 2,
) -> WebhookDeliveryInsights:
    """Summarize deliveries; rates use all records and failure includes failed plus dead-letter."""
    normalized_precision = _normalize_precision(precision)
    normalized_threshold = _normalize_threshold(retry_attempt_threshold)

    overall = _new_totals()
    attempt_sum = 0
    retried_records = 0
    signals = RetrySignals(
        records_with_attempts=0,
        retried_records=0,
        retries_scheduled=0,
        at_or_above_attempt_threshold=0,
        dead_lettered=0,
        breaker_open=0,
        success_responses=0,
        client_error_responses=0,
        server_error_responses=0,
        error_message_present=0,
    )

    event_totals: dict[str, DeliveryTotals] = {}
    webhook_totals: dict[str, DeliveryTotals] = {}
    event_attempt_sums: dict[str, int] = {}
    webhook_attempt_sums: dict[str, int] = {}
    event_retried: dict[str, int] = {}
    webhook_retried: dict[str, int] = {}

    total_records = 0
    invalid_records = 0

    def ensure_group(
        key: str,
        groups: dict[str, DeliveryTotals],
        attempt_sums_by_group: dict[str, int],
        retried_by_group: dict[str, int],
    ) -> None:
        groups.setdefault(key, _new_totals())
        attempt_sums_by_group.setdefault(key, 0)
        retried_by_group.setdefault(key, 0)

    for item in records:
        total_records += 1
        record, coerce_reason = _coerce_record(item)
        if coerce_reason is not None:
            invalid_records += 1

        status = _normalize_status(record.status)
        attempts = _attempt_count(record.attempt_count)
        event = _normalize_text(record.event)
        webhook = _normalize_text(record.webhook_id)

        _add_to_totals(overall, status, attempts)
        attempt_sum += attempts
        was_retried = attempts > 1 or status == "retrying"
        if was_retried:
            retried_records += 1
        if attempts > 0:
            signals.records_with_attempts += 1
        if attempts >= normalized_threshold:
            signals.at_or_above_attempt_threshold += 1
        if record.next_retry_at is not None and status != "success":
            signals.retries_scheduled += 1
        if record.dead_lettered_at is not None or status == "dead_letter":
            signals.dead_lettered += 1
        if status == "breaker_open":
            signals.breaker_open += 1
        if record.error_message is not None and record.error_message.strip():
            signals.error_message_present += 1

        response_code = _finite_float(record.response_status_code)
        if response_code is not None:
            code = int(response_code)
            if 200 <= code < 300:
                signals.success_responses += 1
            elif 400 <= code < 500:
                signals.client_error_responses += 1
            elif 500 <= code < 600:
                signals.server_error_responses += 1

        for key, groups, sums, retried in (
            (event, event_totals, event_attempt_sums, event_retried),
            (webhook, webhook_totals, webhook_attempt_sums, webhook_retried),
        ):
            ensure_group(key, groups, sums, retried)
            _add_to_totals(groups[key], status, attempts)
            sums[key] += attempts
            if was_retried:
                retried[key] += 1

    signals.retried_records = retried_records
    totals = _finalize_totals(overall, attempt_sum, normalized_precision)
    failure_count = totals.failed + totals.dead_letter

    return WebhookDeliveryInsights(
        total_records=total_records,
        invalid_records=invalid_records,
        totals=totals,
        success_rate_percent=_percentage(totals.success, totals.total, normalized_precision),
        failure_rate_percent=_percentage(failure_count, totals.total, normalized_precision),
        retrying_rate_percent=_percentage(totals.retrying, totals.total, normalized_precision),
        dead_letter_rate_percent=_percentage(totals.dead_letter, totals.total, normalized_precision),
        retry_rate_percent=_percentage(retried_records, totals.total, normalized_precision),
        retry_attempt_threshold=normalized_threshold,
        retry_signals=signals,
        by_event=_build_groups(event_totals, event_attempt_sums, event_retried, normalized_precision),
        by_webhook=_build_groups(webhook_totals, webhook_attempt_sums, webhook_retried, normalized_precision),
    )


class WebhookDeliveryInsightsService:
    """Service facade for webhook delivery insights."""

    @staticmethod
    def summarize(
        records: Iterable[WebhookDeliveryRecord | Mapping[str, object]],
        *,
        retry_attempt_threshold: int = 3,
        precision: int = 2,
    ) -> WebhookDeliveryInsights:
        return summarize_webhook_deliveries(
            records,
            retry_attempt_threshold=retry_attempt_threshold,
            precision=precision,
        )


__all__ = [
    "DeliveryGroup",
    "DeliveryTotals",
    "RetrySignals",
    "WebhookDeliveryInsights",
    "WebhookDeliveryInsightsService",
    "WebhookDeliveryRecord",
    "summarize_webhook_deliveries",
]
