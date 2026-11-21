"""Reconciliation checks between SLA outcomes and settlement payment records.

Inputs may be the dataclasses defined here, mappings, or arbitrary objects with
matching attributes. The service is pure and database-free. Uninterpretable
records are reported as ``INVALID_RECORD`` mismatches instead of raising, so a
partially populated operational snapshot can still be analyzed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

MET = "met"
VIOLATED = "violated"
REWARD = "reward"
PENALTY = "penalty"
PENDING = "pending"
CONFIRMED = "confirmed"
FAILED = "failed"

EXPECTED_PAYMENT_TYPE = {MET: REWARD, VIOLATED: PENALTY}


class MismatchType(str, Enum):
    INVALID_RECORD = "invalid_record"
    MISSING_PAYMENT = "missing_payment"
    DUPLICATE_PAYMENT = "duplicate_payment"
    AMOUNT_MISMATCH = "amount_mismatch"
    TYPE_MISMATCH = "type_mismatch"
    PAYMENT_STATUS_MISMATCH = "payment_status_mismatch"
    OUTAGE_MISMATCH = "outage_mismatch"
    OUTCOME_TYPE_MISMATCH = "outcome_type_mismatch"
    UNMATCHED_PAYMENT = "unmatched_payment"


@dataclass(frozen=True)
class SLAOutcome:
    outage_id: str
    amount: int
    payment_type: str
    sla_result_id: int | None = None
    status: str = MET
    is_latest: bool = True


@dataclass(frozen=True)
class PaymentRecord:
    payment_id: str
    amount: int
    payment_type: str
    status: str = CONFIRMED
    sla_result_id: int | None = None
    outage_id: str | None = None


@dataclass(frozen=True)
class ReconciliationMismatch:
    mismatch_type: MismatchType
    outage_id: str | None
    sla_result_id: int | None
    payment_id: str | None
    expected: str | None
    actual: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mismatch_type": self.mismatch_type.value,
            "outage_id": self.outage_id,
            "sla_result_id": self.sla_result_id,
            "payment_id": self.payment_id,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    mismatches: tuple[ReconciliationMismatch, ...]
    outcome_count: int
    payment_count: int
    matched_outcome_count: int

    @property
    def mismatch_count(self) -> int:
        return len(self.mismatches)

    @property
    def is_reconciled(self) -> bool:
        return not self.mismatches

    @property
    def mismatch_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for mismatch in self.mismatches:
            key = mismatch.mismatch_type.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mismatches": [mismatch.to_dict() for mismatch in self.mismatches],
            "outcome_count": self.outcome_count,
            "payment_count": self.payment_count,
            "matched_outcome_count": self.matched_outcome_count,
            "mismatch_count": self.mismatch_count,
            "mismatch_counts": self.mismatch_counts,
            "is_reconciled": self.is_reconciled,
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


def _as_text(value: Any, name: str, *, required: bool = True) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    text = str(value).strip()
    if required and not text:
        raise ValueError(f"{name} must not be blank")
    return text or None


def _as_optional_int(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.lstrip("+-").isdigit():
            return int(candidate)
    raise ValueError(f"{name} must be an integer")


def _as_int(value: Any, name: str) -> int:
    parsed = _as_optional_int(value, name)
    if parsed is None:
        raise ValueError(f"{name} is required")
    return parsed


def _as_choice(value: Any, allowed: set[str], name: str) -> str:
    text = _as_text(value, name)
    normalized = str(text).lower()
    if normalized not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")
    return normalized


def _peek_text(value: Any, names: Iterable[str]) -> str | None:
    try:
        return _as_text(_read(value, names), "value", required=False)
    except (TypeError, ValueError, AttributeError):
        return None


def _peek_int(value: Any, names: Iterable[str]) -> int | None:
    try:
        return _as_optional_int(_read(value, names), "value")
    except (TypeError, ValueError, AttributeError):
        return None


def _coerce_sla_outcome(value: Any) -> SLAOutcome:
    status = _as_choice(_read(value, ("status", "outcome"), MET), {MET, VIOLATED}, "status")
    raw_payment_type = _read(value, ("payment_type", "type"))
    if raw_payment_type is None:
        payment_type = EXPECTED_PAYMENT_TYPE[status]
    else:
        payment_type = _as_choice(raw_payment_type, {REWARD, PENALTY}, "payment_type")
    outcome = SLAOutcome(
        outage_id=str(_as_text(_read(value, ("outage_id", "outage")), "outage_id")),
        amount=_as_int(_read(value, ("amount", "payout_amount"), 0), "amount"),
        payment_type=payment_type,
        sla_result_id=_as_optional_int(_read(value, ("sla_result_id", "id")), "sla_result_id"),
        status=status,
        is_latest=bool(_read(value, ("is_latest",), True)),
    )
    if outcome.amount < 0:
        raise ValueError("amount must be non-negative")
    return outcome


def _coerce_payment(value: Any, index: int) -> PaymentRecord:
    raw_payment_type = _read(value, ("payment_type", "type"))
    if raw_payment_type is None:
        raw_payment_type = REWARD
    payment_id = _as_text(_read(value, ("payment_id", "id", "transaction_hash")), "payment_id", required=False)
    payment = PaymentRecord(
        payment_id=str(payment_id or f"payment-{index}"),
        amount=_as_int(_read(value, ("amount",), 0), "amount"),
        payment_type=_as_choice(raw_payment_type, {REWARD, PENALTY}, "payment_type"),
        status=_as_choice(_read(value, ("status",), CONFIRMED), {PENDING, CONFIRMED, FAILED}, "status"),
        sla_result_id=_as_optional_int(_read(value, ("sla_result_id",)), "sla_result_id"),
        outage_id=_as_text(_read(value, ("outage_id",)), "outage_id", required=False),
    )
    if payment.amount < 0:
        raise ValueError("amount must be non-negative")
    return payment


def _make_mismatch(
    mismatch_type: MismatchType,
    *,
    outcome: SLAOutcome | None = None,
    payment: PaymentRecord | None = None,
    expected: str | None = None,
    actual: str | None = None,
    detail: str = "",
    outage_id_hint: str | None = None,
    sla_result_id_hint: int | None = None,
    payment_id_hint: str | None = None,
) -> ReconciliationMismatch:
    outage_id = outage_id_hint
    sla_result_id = sla_result_id_hint
    payment_id = payment_id_hint
    if outcome is not None:
        outage_id = outcome.outage_id
        sla_result_id = outcome.sla_result_id
    if payment is not None:
        outage_id = outage_id or payment.outage_id
        sla_result_id = sla_result_id if sla_result_id is not None else payment.sla_result_id
        payment_id = payment.payment_id
    return ReconciliationMismatch(
        mismatch_type=mismatch_type,
        outage_id=outage_id,
        sla_result_id=sla_result_id,
        payment_id=payment_id,
        expected=expected,
        actual=actual,
        detail=detail,
    )


def _normalize_records(
    values: Iterable[Any],
    coerce: Any,
    strict: bool,
    mismatches: list[ReconciliationMismatch],
) -> list[Any]:
    normalized: list[Any] = []
    for index, value in enumerate(values):
        try:
            normalized.append(coerce(value, index))
        except (TypeError, ValueError, AttributeError) as exc:
            if strict:
                raise
            mismatches.append(
                _make_mismatch(
                    MismatchType.INVALID_RECORD,
                    outage_id_hint=_peek_text(value, ("outage_id", "outage")),
                    sla_result_id_hint=_peek_int(value, ("sla_result_id", "id")),
                    payment_id_hint=_peek_text(value, ("payment_id", "id", "transaction_hash")),
                    detail=f"record {index} was skipped: {exc}",
                )
            )
    return normalized


def reconcile_sla_payments(
    sla_outcomes: Iterable[Any],
    payment_records: Iterable[Any],
    *,
    require_confirmed: bool = True,
    latest_only: bool = False,
    strict: bool = False,
) -> ReconciliationReport:
    """Compare SLA outcomes with payment records and return typed mismatches.

    Matching prefers ``sla_result_id`` and falls back to ``outage_id`` for
    payments that carry no SLA link. ``require_confirmed`` treats pending
    payments as unsettled; failed payments are always mismatches. Duplicate
    outcomes are included unless ``latest_only`` is set, and ``strict`` turns
    malformed records into ``ValueError`` instead of ``INVALID_RECORD`` rows.

    Complexity is O(outcomes + payments + mismatches); all output is ordered by
    outage, mismatch type, SLA id, and payment id for reproducible reports.
    """
    mismatches: list[ReconciliationMismatch] = []
    outcomes = _normalize_records(sla_outcomes, lambda value, _: _coerce_sla_outcome(value), strict, mismatches)
    payments = _normalize_records(payment_records, _coerce_payment, strict, mismatches)

    payments_by_sla: dict[int, list[PaymentRecord]] = {}
    payments_by_outage: dict[str, list[PaymentRecord]] = {}
    for payment in payments:
        if payment.sla_result_id is not None:
            payments_by_sla.setdefault(payment.sla_result_id, []).append(payment)
        elif payment.outage_id:
            payments_by_outage.setdefault(payment.outage_id, []).append(payment)

    accepted_statuses = {CONFIRMED, PENDING} if not require_confirmed else {CONFIRMED}
    matched_payment_keys: set[int] = set()
    matched_outcome_count = 0

    ordered_outcomes = sorted(
        outcomes,
        key=lambda outcome: (outcome.outage_id, outcome.sla_result_id is None, outcome.sla_result_id or 0),
    )
    for outcome in ordered_outcomes:
        expected_type = EXPECTED_PAYMENT_TYPE[outcome.status]
        if outcome.payment_type != expected_type:
            mismatches.append(
                _make_mismatch(
                    MismatchType.OUTCOME_TYPE_MISMATCH,
                    outcome=outcome,
                    expected=expected_type,
                    actual=outcome.payment_type,
                    detail=f"status '{outcome.status}' normally produces '{expected_type}'",
                )
            )

        candidates: list[PaymentRecord] = []
        if outcome.sla_result_id is not None:
            candidates.extend(payments_by_sla.get(outcome.sla_result_id, []))
        candidates.extend(payments_by_outage.get(outcome.outage_id, []))
        candidates = sorted({payment.payment_id: payment for payment in candidates}.values(), key=lambda p: p.payment_id)

        if latest_only and not outcome.is_latest:
            matched_payment_keys.update(id(payment) for payment in candidates)
            continue

        if not candidates:
            mismatches.append(
                _make_mismatch(
                    MismatchType.MISSING_PAYMENT,
                    outcome=outcome,
                    expected=str(outcome.amount),
                    actual=None,
                    detail="no linked payment record found",
                )
            )
            continue

        matched_outcome_count += 1
        matched_payment_keys.update(id(payment) for payment in candidates)

        if len(candidates) > 1:
            mismatches.append(
                _make_mismatch(
                    MismatchType.DUPLICATE_PAYMENT,
                    outcome=outcome,
                    expected="1",
                    actual=str(len(candidates)),
                    detail="multiple payments linked to one SLA outcome",
                )
            )

        for payment in candidates:
            if payment.status not in accepted_statuses:
                mismatches.append(
                    _make_mismatch(
                        MismatchType.PAYMENT_STATUS_MISMATCH,
                        outcome=outcome,
                        payment=payment,
                        expected="/".join(sorted(accepted_statuses)),
                        actual=payment.status,
                        detail="payment is not in an accepted settlement state",
                    )
                )
            if payment.amount != outcome.amount:
                mismatches.append(
                    _make_mismatch(
                        MismatchType.AMOUNT_MISMATCH,
                        outcome=outcome,
                        payment=payment,
                        expected=str(outcome.amount),
                        actual=str(payment.amount),
                        detail="payment amount differs from SLA outcome amount",
                    )
                )
            if payment.payment_type != outcome.payment_type:
                mismatches.append(
                    _make_mismatch(
                        MismatchType.TYPE_MISMATCH,
                        outcome=outcome,
                        payment=payment,
                        expected=outcome.payment_type,
                        actual=payment.payment_type,
                        detail="payment type differs from SLA payment type",
                    )
                )
            if payment.outage_id and payment.outage_id != outcome.outage_id:
                mismatches.append(
                    _make_mismatch(
                        MismatchType.OUTAGE_MISMATCH,
                        outcome=outcome,
                        payment=payment,
                        expected=outcome.outage_id,
                        actual=payment.outage_id,
                        detail="payment points at a different outage",
                    )
                )

    for payment in sorted(payments, key=lambda item: item.payment_id):
        if id(payment) not in matched_payment_keys:
            mismatches.append(
                _make_mismatch(
                    MismatchType.UNMATCHED_PAYMENT,
                    payment=payment,
                    expected=None,
                    actual=payment.status,
                    detail="payment has no corresponding SLA outcome",
                )
            )

    mismatches.sort(
        key=lambda mismatch: (
            mismatch.outage_id or "",
            mismatch.mismatch_type.value,
            mismatch.sla_result_id if mismatch.sla_result_id is not None else -1,
            mismatch.payment_id or "",
        )
    )
    return ReconciliationReport(
        mismatches=tuple(mismatches),
        outcome_count=len(outcomes),
        payment_count=len(payments),
        matched_outcome_count=matched_outcome_count,
    )


detect_reconciliation_mismatches = reconcile_sla_payments
