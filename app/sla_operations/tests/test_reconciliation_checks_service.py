"""Tests for the SLA operations reconciliation checks service.

These tests exercise ``app.sla_operations.services.reconciliation_checks``
in isolation from the core application state. They use lightweight
dataclass-like fixtures defined locally so that no database, ORM models,
or FastAPI app state is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

import pytest

from app.sla_operations.services.reconciliation_checks import (
    MismatchType,
    ReconciliationCheckService,
    ReconciliationMismatch,
    ReconciliationRecord,
    ReconciliationResult,
    SettlementRecord,
)


# ---------------------------------------------------------------------------
# Local fixtures / helpers
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def make_reconciliation_record(
    *,
    record_id: str = "rec-1",
    outage_id: str = "outage-1",
    amount: Decimal = Decimal("100.00"),
    currency: str = "USD",
    payment_type: str = "penalty",
    status: str = "confirmed",
    created_at: Optional[datetime] = None,
) -> ReconciliationRecord:
    return ReconciliationRecord(
        record_id=record_id,
        outage_id=outage_id,
        amount=amount,
        currency=currency,
        payment_type=payment_type,
        status=status,
        created_at=created_at or _utc(2024, 1, 1),
    )


def make_settlement_record(
    *,
    settlement_id: str = "set-1",
    outage_id: str = "outage-1",
    amount: Decimal = Decimal("100.00"),
    currency: str = "USD",
    payment_type: str = "penalty",
    status: str = "confirmed",
    created_at: Optional[datetime] = None,
) -> SettlementRecord:
    return SettlementRecord(
        settlement_id=settlement_id,
        outage_id=outage_id,
        amount=amount,
        currency=currency,
        payment_type=payment_type,
        status=status,
        created_at=created_at or _utc(2024, 1, 1),
    )


@pytest.fixture()
def service() -> ReconciliationCheckService:
    return ReconciliationCheckService()


# ---------------------------------------------------------------------------
# Primary behavior: matched records
# ---------------------------------------------------------------------------


def test_matched_records_produce_no_mismatches(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record()
    set_rec = make_settlement_record()

    result = service.check([rec], [set_rec])

    assert isinstance(result, ReconciliationResult)
    assert result.mismatches == []
    assert result.is_balanced is True
    assert result.checked_count == 1


def test_empty_inputs_produce_balanced_result(service: ReconciliationCheckService) -> None:
    result = service.check([], [])

    assert result.mismatches == []
    assert result.is_balanced is True
    assert result.checked_count == 0


# ---------------------------------------------------------------------------
# Missing / unmatched payments
# ---------------------------------------------------------------------------


def test_missing_payment_detected(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(record_id="rec-1")

    result = service.check([rec], [])

    assert result.is_balanced is False
    assert len(result.mismatches) == 1
    mismatch = result.mismatches[0]
    assert mismatch.mismatch_type is MismatchType.MISSING_PAYMENT
    assert mismatch.record_id == "rec-1"


def test_unmatched_payment_detected(service: ReconciliationCheckService) -> None:
    set_rec = make_settlement_record(settlement_id="set-1")

    result = service.check([], [set_rec])

    assert result.is_balanced is False
    assert len(result.mismatches) == 1
    mismatch = result.mismatches[0]
    assert mismatch.mismatch_type is MismatchType.UNMATCHED_PAYMENT
    assert mismatch.settlement_id == "set-1"


def test_duplicate_payment_detected(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(record_id="rec-1")
    set_a = make_settlement_record(settlement_id="set-1")
    set_b = make_settlement_record(settlement_id="set-2")

    result = service.check([rec], [set_a, set_b])

    assert result.is_balanced is False
    types = {m.mismatch_type for m in result.mismatches}
    assert MismatchType.DUPLICATE_PAYMENT in types
    duplicate = next(
        m for m in result.mismatches if m.mismatch_type is MismatchType.DUPLICATE_PAYMENT
    )
    assert duplicate.record_id == "rec-1"
    assert set(duplicate.settlement_ids) == {"set-1", "set-2"}


# ---------------------------------------------------------------------------
# Field mismatches
# ---------------------------------------------------------------------------


def test_amount_mismatch_detected(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(amount=Decimal("100.00"))
    set_rec = make_settlement_record(amount=Decimal("99.99"))

    result = service.check([rec], [set_rec])

    assert result.is_balanced is False
    assert len(result.mismatches) == 1
    mismatch = result.mismatches[0]
    assert mismatch.mismatch_type is MismatchType.AMOUNT_MISMATCH
    assert mismatch.record_id == "rec-1"
    assert mismatch.settlement_id == "set-1"


def test_currency_mismatch_detected(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(currency="USD")
    set_rec = make_settlement_record(currency="EUR")

    result = service.check([rec], [set_rec])

    assert result.is_balanced is False
    assert len(result.mismatches) == 1
    assert result.mismatches[0].mismatch_type is MismatchType.CURRENCY_MISMATCH


def test_payment_type_mismatch_detected(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(payment_type="penalty")
    set_rec = make_settlement_record(payment_type="credit")

    result = service.check([rec], [set_rec])

    assert result.is_balanced is False
    assert len(result.mismatches) == 1
    assert result.mismatches[0].mismatch_type is MismatchType.PAYMENT_TYPE_MISMATCH


def test_status_mismatch_detected(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(status="confirmed")
    set_rec = make_settlement_record(status="pending")

    result = service.check([rec], [set_rec])

    assert result.is_balanced is False
    assert len(result.mismatches) == 1
    assert result.mismatches[0].mismatch_type is MismatchType.STATUS_MISMATCH


def test_outage_mismatch_detected(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(outage_id="outage-1")
    set_rec = make_settlement_record(outage_id="outage-2")

    result = service.check([rec], [set_rec])

    assert result.is_balanced is False
    assert len(result.mismatches) == 1
    assert result.mismatches[0].mismatch_type is MismatchType.OUTAGE_MISMATCH


# ---------------------------------------------------------------------------
# Invalid records / error handling
# ---------------------------------------------------------------------------


def test_invalid_reconciliation_record_detected(service: ReconciliationCheckService) -> None:
    # Negative amount is invalid.
    rec = make_reconciliation_record(amount=Decimal("-1.00"))

    result = service.check([rec], [])

    assert result.is_balanced is False
    types = {m.mismatch_type for m in result.mismatches}
    assert MismatchType.INVALID_RECORD in types


def test_invalid_settlement_record_detected(service: ReconciliationCheckService) -> None:
    set_rec = make_settlement_record(amount=Decimal("-5.00"))

    result = service.check([], [set_rec])

    assert result.is_balanced is False
    types = {m.mismatch_type for m in result.mismatches}
    assert MismatchType.INVALID_RECORD in types


def test_invalid_record_does_not_raise(service: ReconciliationCheckService) -> None:
    # Service should report invalid records rather than raising.
    rec = make_reconciliation_record(amount=Decimal("-1.00"))
    set_rec = make_settlement_record(amount=Decimal("-1.00"))

    result = service.check([rec], [set_rec])

    assert isinstance(result, ReconciliationResult)
    assert result.is_balanced is False


# ---------------------------------------------------------------------------
# Edge cases: latest_only
# ---------------------------------------------------------------------------


def test_latest_only_selects_most_recent_settlement(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(created_at=_utc(2024, 1, 1))
    older = make_settlement_record(
        settlement_id="set-old",
        amount=Decimal("50.00"),
        created_at=_utc(2024, 1, 1),
    )
    newer = make_settlement_record(
        settlement_id="set-new",
        amount=Decimal("100.00"),
        created_at=_utc(2024, 2, 1),
    )

    result = service.check([rec], [older, newer], latest_only=True)

    assert result.is_balanced is True
    assert result.mismatches == []


def test_latest_only_reports_mismatch_against_latest(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(amount=Decimal("100.00"))
    older = make_settlement_record(
        settlement_id="set-old",
        amount=Decimal("100.00"),
        created_at=_utc(2024, 1, 1),
    )
    newer = make_settlement_record(
        settlement_id="set-new",
        amount=Decimal("75.00"),
        created_at=_utc(2024, 2, 1),
    )

    result = service.check([rec], [older, newer], latest_only=True)

    assert result.is_balanced is False
    assert len(result.mismatches) == 1
    mismatch = result.mismatches[0]
    assert mismatch.mismatch_type is MismatchType.AMOUNT_MISMATCH
    assert mismatch.settlement_id == "set-new"


# ---------------------------------------------------------------------------
# Edge cases: require_confirmed
# ---------------------------------------------------------------------------


def test_require_confirmed_skips_pending_settlements(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(status="confirmed")
    pending = make_settlement_record(settlement_id="set-pending", status="pending")

    result = service.check([rec], [pending], require_confirmed=True)

    # Pending settlement is ignored, so the reconciliation record is missing a payment.
    assert result.is_balanced is False
    types = {m.mismatch_type for m in result.mismatches}
    assert MismatchType.MISSING_PAYMENT in types


def test_require_confirmed_matches_confirmed_settlements(
    service: ReconciliationCheckService,
) -> None:
    rec = make_reconciliation_record(status="confirmed")
    confirmed = make_settlement_record(status="confirmed")

    result = service.check([rec], [confirmed], require_confirmed=True)

    assert result.is_balanced is True


def test_require_confirmed_skips_pending_reconciliation_records(
    service: ReconciliationCheckService,
) -> None:
    rec = make_reconciliation_record(status="pending")
    confirmed = make_settlement_record(status="confirmed")

    result = service.check([rec], [confirmed], require_confirmed=True)

    # Pending reconciliation record is skipped; settlement becomes unmatched.
    assert result.is_balanced is False
    types = {m.mismatch_type for m in result.mismatches}
    assert MismatchType.UNMATCHED_PAYMENT in types


# ---------------------------------------------------------------------------
# Edge cases: strict mode
# ---------------------------------------------------------------------------


def test_strict_mode_flags_currency_mismatch(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(currency="USD")
    set_rec = make_settlement_record(currency="EUR")

    result = service.check([rec], [set_rec], strict=True)

    assert result.is_balanced is False
    types = {m.mismatch_type for m in result.mismatches}
    assert MismatchType.CURRENCY_MISMATCH in types


def test_non_strict_mode_ignores_currency_mismatch(
    service: ReconciliationCheckService,
) -> None:
    rec = make_reconciliation_record(currency="USD")
    set_rec = make_settlement_record(currency="EUR")

    result = service.check([rec], [set_rec], strict=False)

    # Non-strict mode does not treat currency differences as mismatches.
    assert result.is_balanced is True


def test_strict_mode_flags_payment_type_mismatch(
    service: ReconciliationCheckService,
) -> None:
    rec = make_reconciliation_record(payment_type="penalty")
    set_rec = make_settlement_record(payment_type="credit")

    result = service.check([rec], [set_rec], strict=True)

    assert result.is_balanced is False
    types = {m.mismatch_type for m in result.mismatches}
    assert MismatchType.PAYMENT_TYPE_MISMATCH in types


# ---------------------------------------------------------------------------
# Multiple records / combined scenarios
# ---------------------------------------------------------------------------


def test_multiple_records_aggregate_mismatches(service: ReconciliationCheckService) -> None:
    rec_ok = make_reconciliation_record(record_id="rec-ok")
    rec_missing = make_reconciliation_record(record_id="rec-missing")
    set_ok = make_settlement_record(settlement_id="set-ok")
    set_extra = make_settlement_record(settlement_id="set-extra")

    result = service.check([rec_ok, rec_missing], [set_ok, set_extra])

    assert result.is_balanced is False
    types = {m.mismatch_type for m in result.mismatches}
    assert MismatchType.MISSING_PAYMENT in types
    assert MismatchType.UNMATCHED_PAYMENT in types


def test_result_checked_count_reflects_inputs(service: ReconciliationCheckService) -> None:
    records = [
        make_reconciliation_record(record_id="rec-1"),
        make_reconciliation_record(record_id="rec-2"),
    ]
    settlements = [
        make_settlement_record(settlement_id="set-1"),
        make_settlement_record(settlement_id="set-2"),
    ]

    result = service.check(records, settlements)

    assert result.checked_count == 2
    assert result.is_balanced is True


def test_mismatch_has_readable_message(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(amount=Decimal("100.00"))
    set_rec = make_settlement_record(amount=Decimal("50.00"))

    result = service.check([rec], [set_rec])

    assert len(result.mismatches) == 1
    mismatch = result.mismatches[0]
    assert isinstance(mismatch.message, str)
    assert mismatch.message  # non-empty


def test_service_is_deterministic(service: ReconciliationCheckService) -> None:
    rec = make_reconciliation_record(amount=Decimal("100.00"))
    set_rec = make_settlement_record(amount=Decimal("50.00"))

    first = service.check([rec], [set_rec])
    second = service.check([rec], [set_rec])

    assert [m.mismatch_type for m in first.mismatches] == [
        m.mismatch_type for m in second.mismatches
    ]
    assert first.is_balanced == second.is_balanced