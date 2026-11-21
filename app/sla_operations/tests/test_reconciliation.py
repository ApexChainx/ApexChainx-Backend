from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.reconciliation import (
    PaymentSlaReconciliationCheck,
    ReconciliationCheckStatus,
    ReconciliationMismatchReport,
)


def test_payment_sla_reconciliation_check_valid_mismatch():
    check = PaymentSlaReconciliationCheck(
        check_id="recon-1",
        status=ReconciliationCheckStatus.MISMATCHED,
        outage_id="outage-1",
        sla_result_id=10,
        payment_id="pay-1",
        expected_amount_minor=1500,
        actual_amount_minor=1200,
        currency="usd",
        expected_payment_status="confirmed",
        actual_payment_status="pending",
        reason_codes=["amount_mismatch", "status_mismatch"],
        checked_at=datetime(2026, 11, 20, tzinfo=UTC),
    )
    assert check.currency == "USD"
    assert check.reason_codes == ["amount_mismatch", "status_mismatch"]


def test_reconciliation_check_rejects_matched_with_reasons():
    with pytest.raises(ValidationError):
        PaymentSlaReconciliationCheck(
            check_id="recon-2",
            status=ReconciliationCheckStatus.MATCHED,
            outage_id="outage-2",
            reason_codes=["should_not_exist"],
        )


def test_reconciliation_check_rejects_non_matched_without_reasons_and_missing_ids():
    with pytest.raises(ValidationError):
        PaymentSlaReconciliationCheck(
            check_id="recon-3",
            status=ReconciliationCheckStatus.MISMATCHED,
            outage_id="outage-3",
            reason_codes=[],
        )
    with pytest.raises(ValidationError):
        PaymentSlaReconciliationCheck(
            check_id="recon-4",
            status=ReconciliationCheckStatus.MISSING_ACTUAL,
            reason_codes=["missing_actual"],
        )


def test_reconciliation_check_boundary_missing_states():
    with pytest.raises(ValidationError):
        PaymentSlaReconciliationCheck(
            check_id="recon-5",
            status=ReconciliationCheckStatus.MISSING_EXPECTED,
            payment_id="pay-5",
            expected_amount_minor=100,
            reason_codes=["missing_expected"],
        )
    with pytest.raises(ValidationError):
        PaymentSlaReconciliationCheck(
            check_id="recon-6",
            status=ReconciliationCheckStatus.MISSING_ACTUAL,
            payment_id="pay-6",
            actual_amount_minor=100,
            reason_codes=["missing_actual"],
        )


def test_reconciliation_mismatch_report_valid_and_consistent():
    matched = PaymentSlaReconciliationCheck(
        check_id="a", status=ReconciliationCheckStatus.MATCHED, outage_id="o1"
    )
    mismatched = PaymentSlaReconciliationCheck(
        check_id="b",
        status=ReconciliationCheckStatus.MISMATCHED,
        outage_id="o2",
        reason_codes=["amount_mismatch"],
    )
    report = ReconciliationMismatchReport(
        report_id="batch-1",
        checks=[matched, mismatched],
        total_checks=2,
        matched_count=1,
        mismatch_count=1,
        mismatches=[mismatched],
    )
    assert report.mismatches[0].check_id == "b"


def test_reconciliation_mismatch_report_rejects_inconsistent_counts_or_mismatches():
    matched = PaymentSlaReconciliationCheck(
        check_id="a", status=ReconciliationCheckStatus.MATCHED, outage_id="o1"
    )
    mismatched = PaymentSlaReconciliationCheck(
        check_id="b",
        status=ReconciliationCheckStatus.MISSING_ACTUAL,
        outage_id="o2",
        reason_codes=["missing_actual"],
    )
    with pytest.raises(ValidationError):
        ReconciliationMismatchReport(
            report_id="batch-bad",
            checks=[matched, mismatched],
            total_checks=2,
            matched_count=2,
            mismatch_count=0,
            mismatches=[],
        )
    with pytest.raises(ValidationError):
        ReconciliationMismatchReport(
            report_id="batch-bad-2",
            checks=[matched, mismatched],
            total_checks=2,
            matched_count=1,
            mismatch_count=1,
            mismatches=[],
        )
