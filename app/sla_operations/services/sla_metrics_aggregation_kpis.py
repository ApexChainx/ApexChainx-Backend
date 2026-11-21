from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SlaKpiAggregate:
    """Deterministic KPI aggregate computed from SLA records."""

    total_records: int
    met_count: int
    violated_count: int
    unknown_status_count: int
    violation_rate: float
    avg_mttr_minutes: float
    total_reward_amount: float
    total_penalty_amount: float
    net_payout_amount: float


def _as_mapping(record: Any) -> Mapping[str, Any]:
    if isinstance(record, Mapping):
        return record
    if hasattr(record, "__dict__"):
        return vars(record)
    raise TypeError("SLA metric records must be mappings or objects with attributes")


def _get_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_status(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _normalize_payment_type(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def aggregate_sla_metrics(records: Iterable[Any] | None) -> SlaKpiAggregate:
    """Compute SLA KPI aggregates from in-memory/provided datasets.

    The function is self-contained, deterministic for a given input order/content,
    and ignores malformed numeric fields instead of raising unhandled exceptions.
    Unknown or missing statuses are counted separately and excluded from met/violated
    tallies.
    """

    total_records = 0
    met_count = 0
    violated_count = 0
    unknown_status_count = 0
    mttr_sum = 0.0
    mttr_samples = 0
    total_reward_amount = 0.0
    total_penalty_amount = 0.0

    for record in records or []:
        try:
            # Validate supported record shapes early; unsupported entries are ignored.
            _as_mapping(record)
        except TypeError:
            continue

        total_records += 1
        status = _normalize_status(_get_value(record, "status"))
        if status == "met":
            met_count += 1
        elif status == "violated":
            violated_count += 1
        else:
            unknown_status_count += 1

        mttr = _coerce_float(_get_value(record, "mttr_minutes"))
        if mttr is not None and mttr >= 0:
            mttr_sum += mttr
            mttr_samples += 1

        amount = _coerce_float(_get_value(record, "amount"))
        if amount is not None:
            payment_type = _normalize_payment_type(_get_value(record, "payment_type"))
            if payment_type == "reward":
                total_reward_amount += abs(amount)
            elif payment_type == "penalty":
                total_penalty_amount += abs(amount)
            elif status == "met":
                total_reward_amount += abs(amount)
            elif status == "violated":
                total_penalty_amount += abs(amount)

    classified_total = met_count + violated_count
    violation_rate = (violated_count / classified_total) if classified_total else 0.0
    avg_mttr_minutes = (mttr_sum / mttr_samples) if mttr_samples else 0.0
    net_payout_amount = total_reward_amount - total_penalty_amount

    return SlaKpiAggregate(
        total_records=total_records,
        met_count=met_count,
        violated_count=violated_count,
        unknown_status_count=unknown_status_count,
        violation_rate=round(violation_rate, 6),
        avg_mttr_minutes=round(avg_mttr_minutes, 6),
        total_reward_amount=round(total_reward_amount, 6),
        total_penalty_amount=round(total_penalty_amount, 6),
        net_payout_amount=round(net_payout_amount, 6),
    )


def aggregate_sla_metrics_as_dict(records: Iterable[Any] | None) -> dict[str, int | float]:
    """Convenience wrapper exposing aggregates as a plain dictionary."""

    aggregate = aggregate_sla_metrics(records)
    return {
        "total_records": aggregate.total_records,
        "met_count": aggregate.met_count,
        "violated_count": aggregate.violated_count,
        "unknown_status_count": aggregate.unknown_status_count,
        "violation_rate": aggregate.violation_rate,
        "avg_mttr_minutes": aggregate.avg_mttr_minutes,
        "total_reward_amount": aggregate.total_reward_amount,
        "total_penalty_amount": aggregate.total_penalty_amount,
        "net_payout_amount": aggregate.net_payout_amount,
    }


__all__ = ["SlaKpiAggregate", "aggregate_sla_metrics", "aggregate_sla_metrics_as_dict"]
