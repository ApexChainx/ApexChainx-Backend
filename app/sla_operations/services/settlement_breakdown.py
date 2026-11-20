"""Deterministic reward, penalty, and net settlement breakdowns."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ValidationError

_REWARD_TYPES = frozenset({"reward", "credit", "payout", "bonus"})
_PENALTY_TYPES = frozenset({"penalty", "debit", "charge", "fine"})
_NO_SETTLEMENT_TYPES = frozenset({"none", "no_settlement", "neutral", "zero"})
_STATUS_ALIASES = {
    "settled": "settled",
    "confirmed": "settled",
    "completed": "settled",
    "pending": "pending",
    "queued": "pending",
    "failed": "failed",
    "failure": "failed",
}


class SettlementEntry(BaseModel):
    """Settlement input; unsigned amounts require an explicit payment type."""

    outage_id: str = ""
    severity: str | None = None
    status: str | None = None
    payment_type: str | None = None
    amount: float | None = None
    currency: str | None = None


class SettlementTotals(BaseModel):
    reward_count: int = 0
    reward_amount: float = 0.0
    penalty_count: int = 0
    penalty_amount: float = 0.0
    net_amount: float = 0.0
    no_settlement_count: int = 0
    excluded_count: int = 0
    transaction_count: int = 0


class SettlementLine(BaseModel):
    outage_id: str
    severity: str
    status: str
    currency: str
    category: Literal["reward", "penalty", "none", "excluded"]
    amount: float
    reason_code: str | None = None


class SettlementReport(BaseModel):
    total_records: int
    totals: SettlementTotals
    by_status: dict[str, SettlementTotals]
    by_severity: dict[str, SettlementTotals]
    by_currency: dict[str, SettlementTotals]
    lines: list[SettlementLine]


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


def _normalize_currency(value: object, default: str) -> str:
    return _normalize_text(value, default).upper()


def _normalize_status(value: object) -> str:
    text = _normalize_text(value, "unknown")
    return _STATUS_ALIASES.get(text, text)


def _coerce_entry(item: object) -> tuple[SettlementEntry, str | None]:
    try:
        if isinstance(item, SettlementEntry):
            return item, None
        if isinstance(item, Mapping):
            return SettlementEntry.model_validate(dict(item)), None
        return SettlementEntry.model_validate(item), None
    except (ValidationError, TypeError, ValueError):
        return SettlementEntry(), "invalid_record"


def _classify(entry: SettlementEntry) -> tuple[Literal["reward", "penalty", "none", "excluded"], float, str | None]:
    amount = _finite_float(entry.amount)
    if amount is None:
        return "excluded", 0.0, "invalid_amount"

    payment_type = _normalize_text(entry.payment_type, "")
    if payment_type:
        if payment_type in _REWARD_TYPES:
            return "reward", abs(amount), None
        if payment_type in _PENALTY_TYPES:
            return "penalty", abs(amount), None
        if payment_type in _NO_SETTLEMENT_TYPES:
            return "none", 0.0, None
        return "excluded", 0.0, "invalid_payment_type"

    if amount > 0:
        return "reward", amount, None
    if amount < 0:
        return "penalty", abs(amount), None
    return "none", 0.0, None


def _new_totals() -> SettlementTotals:
    return SettlementTotals()


def _add_to_totals(
    totals: SettlementTotals,
    category: Literal["reward", "penalty", "none", "excluded"],
    magnitude: float,
) -> None:
    if category == "reward":
        totals.reward_count += 1
        totals.reward_amount += magnitude
    elif category == "penalty":
        totals.penalty_count += 1
        totals.penalty_amount += magnitude
    elif category == "none":
        totals.no_settlement_count += 1
    else:
        totals.excluded_count += 1


def _finalize_totals(totals: SettlementTotals, precision: int) -> SettlementTotals:
    return SettlementTotals(
        reward_count=totals.reward_count,
        reward_amount=_round(totals.reward_amount, precision),
        penalty_count=totals.penalty_count,
        penalty_amount=_round(totals.penalty_amount, precision),
        net_amount=_round(totals.reward_amount - totals.penalty_amount, precision),
        no_settlement_count=totals.no_settlement_count,
        excluded_count=totals.excluded_count,
        transaction_count=totals.reward_count + totals.penalty_count,
    )


def _sorted_totals(groups: Mapping[str, SettlementTotals], precision: int) -> dict[str, SettlementTotals]:
    return {key: _finalize_totals(groups[key], precision) for key in sorted(groups)}


def build_settlement_breakdown(
    entries: Iterable[SettlementEntry | Mapping[str, object]],
    *,
    default_currency: str = "USDC",
    precision: int = 2,
) -> SettlementReport:
    """Build settlement totals; net amount is rewards minus penalty magnitudes."""
    normalized_precision = _normalize_precision(precision)
    normalized_default_currency = _normalize_currency(default_currency, "USDC")

    overall = _new_totals()
    status_groups: dict[str, SettlementTotals] = {}
    severity_groups: dict[str, SettlementTotals] = {}
    currency_groups: dict[str, SettlementTotals] = {}
    lines: list[SettlementLine] = []
    total_records = 0

    for item in entries:
        total_records += 1
        entry, coerce_reason = _coerce_entry(item)
        category, magnitude, reason_code = _classify(entry)
        if coerce_reason is not None:
            category = "excluded"
            magnitude = 0.0
            reason_code = coerce_reason

        status = _normalize_status(entry.status)
        severity = _normalize_text(entry.severity)
        currency = _normalize_currency(entry.currency, normalized_default_currency)

        _add_to_totals(overall, category, magnitude)
        for groups, key in ((status_groups, status), (severity_groups, severity), (currency_groups, currency)):
            group = groups.get(key)
            if group is None:
                group = _new_totals()
                groups[key] = group
            _add_to_totals(group, category, magnitude)

        lines.append(
            SettlementLine(
                outage_id=entry.outage_id,
                severity=severity,
                status=status,
                currency=currency,
                category=category,
                amount=_round(magnitude, normalized_precision),
                reason_code=reason_code,
            )
        )

    lines.sort(key=lambda line: (line.outage_id, line.severity, line.status, line.category, line.currency))

    return SettlementReport(
        total_records=total_records,
        totals=_finalize_totals(overall, normalized_precision),
        by_status=_sorted_totals(status_groups, normalized_precision),
        by_severity=_sorted_totals(severity_groups, normalized_precision),
        by_currency=_sorted_totals(currency_groups, normalized_precision),
        lines=lines,
    )


class SettlementBreakdownService:
    """Service facade for settlement breakdowns."""

    @staticmethod
    def build(
        entries: Iterable[SettlementEntry | Mapping[str, object]],
        *,
        default_currency: str = "USDC",
        precision: int = 2,
    ) -> SettlementReport:
        return build_settlement_breakdown(entries, default_currency=default_currency, precision=precision)


__all__ = [
    "SettlementBreakdownService",
    "SettlementEntry",
    "SettlementLine",
    "SettlementReport",
    "SettlementTotals",
    "build_settlement_breakdown",
]
