from app.sla_operations.services.reconciliation_checks import (
    MismatchType,
    detect_reconciliation_mismatches,
    reconcile_sla_payments,
)


def _outcome(sla_result_id=1, outage_id="outage-1", amount=100, payment_type="reward", status="met", **extra):
    return {
        "id": sla_result_id,
        "outage_id": outage_id,
        "amount": amount,
        "payment_type": payment_type,
        "status": status,
        **extra,
    }


def _payment(payment_id="p-1", sla_result_id=1, outage_id="outage-1", amount=100, payment_type="reward", status="confirmed"):
    return {
        "id": payment_id,
        "sla_result_id": sla_result_id,
        "outage_id": outage_id,
        "amount": amount,
        "type": payment_type,
        "status": status,
    }


def _types(report):
    return {mismatch.mismatch_type.value for mismatch in report.mismatches}


def test_matching_outcome_and_payment_is_reconciled():
    report = reconcile_sla_payments([_outcome()], [_payment()])

    assert report.is_reconciled
    assert report.outcome_count == 1
    assert report.payment_count == 1
    assert report.matched_outcome_count == 1
    assert report.mismatch_count == 0
    assert report.mismatch_counts == {}
    assert report.to_dict()["is_reconciled"] is True


def test_amount_type_and_status_mismatches_are_detected():
    outcome = _outcome(amount=100, payment_type="penalty", status="violated")
    payment = _payment(amount=90, payment_type="reward", status="pending")

    report = reconcile_sla_payments([outcome], [payment])

    assert _types(report) == {
        MismatchType.AMOUNT_MISMATCH.value,
        MismatchType.TYPE_MISMATCH.value,
        MismatchType.PAYMENT_STATUS_MISMATCH.value,
    }
    amount_mismatch = next(m for m in report.mismatches if m.mismatch_type is MismatchType.AMOUNT_MISMATCH)
    assert amount_mismatch.expected == "100"
    assert amount_mismatch.actual == "90"


def test_unlinked_payment_matches_by_outage_and_orphan_is_reported():
    outcomes = [_outcome(), _outcome(sla_result_id=None, outage_id="outage-2")]
    payments = [
        _payment(payment_id="p-unlinked", sla_result_id=None, outage_id="outage-2"),
        _payment(payment_id="p-orphan", sla_result_id=None, outage_id="outage-999"),
    ]

    report = reconcile_sla_payments(outcomes, payments)

    assert report.matched_outcome_count == 1
    assert _types(report) == {MismatchType.MISSING_PAYMENT.value, MismatchType.UNMATCHED_PAYMENT.value}
    orphan = next(m for m in report.mismatches if m.mismatch_type is MismatchType.UNMATCHED_PAYMENT)
    assert orphan.payment_id == "p-orphan"


def test_duplicate_payments_and_outage_mismatch_are_reported():
    payments = [
        _payment(payment_id="p-1"),
        _payment(payment_id="p-2", outage_id="outage-other"),
    ]

    report = reconcile_sla_payments([_outcome()], payments)

    assert _types(report) == {MismatchType.DUPLICATE_PAYMENT.value, MismatchType.OUTAGE_MISMATCH.value}
    duplicate = next(m for m in report.mismatches if m.mismatch_type is MismatchType.DUPLICATE_PAYMENT)
    assert duplicate.actual == "2"


def test_pending_payment_can_be_accepted():
    report = reconcile_sla_payments([_outcome()], [_payment(status="pending")], require_confirmed=False)

    assert report.is_reconciled


def test_failed_payment_is_always_a_mismatch():
    report = reconcile_sla_payments([_outcome()], [_payment(status="failed")], require_confirmed=False)

    assert _types(report) == {MismatchType.PAYMENT_STATUS_MISMATCH.value}


def test_outcome_status_type_conflict_is_flagged():
    report = reconcile_sla_payments([_outcome(status="violated", payment_type="reward")], [])

    assert _types(report) == {MismatchType.OUTCOME_TYPE_MISMATCH.value, MismatchType.MISSING_PAYMENT.value}


def test_empty_datasets_are_reconciled():
    report = reconcile_sla_payments([], [])

    assert report.is_reconciled
    assert report.outcome_count == 0
    assert report.payment_count == 0
    assert report.matched_outcome_count == 0
    assert report.to_dict()["mismatch_counts"] == {}


def test_invalid_records_are_reported_and_strict_mode_raises():
    outcomes = [_outcome(outage_id=""), _outcome(sla_result_id=1)]
    payments = [_payment(amount="not-a-number")]

    report = reconcile_sla_payments(outcomes, payments)

    assert report.mismatch_counts[MismatchType.INVALID_RECORD.value] == 2
    assert report.outcome_count == 1
    assert report.payment_count == 0

    try:
        reconcile_sla_payments(outcomes, payments, strict=True)
    except ValueError as exc:
        assert "outage_id" in str(exc)
    else:
        raise AssertionError("strict mode should raise for malformed records")


def test_latest_only_skips_stale_outcomes_without_unmatched_payments():
    stale = _outcome(sla_result_id=1, is_latest=False)
    current = _outcome(sla_result_id=2, outage_id="outage-2")
    payments = [_payment(payment_id="p-stale", sla_result_id=1), _payment(payment_id="p-current", sla_result_id=2)]

    report = reconcile_sla_payments([stale, current], payments, latest_only=True)

    assert report.is_reconciled
    assert report.outcome_count == 2
    assert report.matched_outcome_count == 1


def test_output_is_deterministic_regardless_of_input_order():
    outcomes = [_outcome(sla_result_id=2, outage_id="outage-2"), _outcome(sla_result_id=1)]
    payments = [_payment(payment_id="p-2", sla_result_id=2, outage_id="outage-2"), _payment(payment_id="p-1")]

    first = reconcile_sla_payments(outcomes, payments)
    second = reconcile_sla_payments(list(reversed(outcomes)), list(reversed(payments)))

    assert first.to_dict() == second.to_dict()
    assert detect_reconciliation_mismatches([], []).is_reconciled
