"""Regression tests for build_tx_memo_from_result (#356)."""

import pytest

from app.models.sla import SLAResult
from app.services.contracts.translation import build_tx_memo_from_result


def _make_result(outage_id: str) -> SLAResult:
    return SLAResult(
        outage_id=outage_id,
        status="violated",
        mttr_minutes=90,
        threshold_minutes=60,
        amount=100,
        payment_type="penalty",
        rating="poor",
    )


def test_ascii_outage_id_truncates_and_fits():
    result = _make_result("outage-id-that-is-quite-long-123456")
    memo = build_tx_memo_from_result(result)
    assert len(memo.encode("utf-8")) <= 28


def test_multibyte_outage_id_valid_or_raised():
    # Each 'é' is 2 UTF-8 bytes — a single-char agg can still overflow.
    multibyte_id = "é" * 20
    result = _make_result(multibyte_id)
    try:
        memo = build_tx_memo_from_result(result)
    except ValueError as exc:
        assert "28" in str(exc)
    else:
        assert len(memo.encode("utf-8")) <= 28