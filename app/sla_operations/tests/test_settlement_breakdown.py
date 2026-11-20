from app.sla_operations.services.settlement_breakdown import (
    SettlementBreakdownService,
    SettlementEntry,
    build_settlement_breakdown,
)


def test_build_settlement_breakdown_totals_rewards_penalties_and_net():
    entries = [
        {
            "outage_id": "r1",
            "severity": "critical",
            "status": "confirmed",
            "payment_type": "reward",
            "amount": 750.0,
            "currency": "usdc",
        },
        {
            "outage_id": "p1",
            "severity": "critical",
            "status": "settled",
            "payment_type": "penalty",
            "amount": 200.0,
        },
        {"outage_id": "p2", "severity": "high", "status": "pending", "amount": -50.0},
        {"outage_id": "z", "severity": "low", "status": "failed", "amount": 0.0},
    ]

    report = build_settlement_breakdown(entries)

    assert report.total_records == 4
    assert report.totals.reward_count == 1
    assert report.totals.reward_amount == 750.0
    assert report.totals.penalty_count == 2
    assert report.totals.penalty_amount == 250.0
    assert report.totals.net_amount == 500.0
    assert report.totals.no_settlement_count == 1
    assert report.totals.excluded_count == 0
    assert report.totals.transaction_count == 3
    assert list(report.by_status) == ["confirmed", "failed", "pending", "settled"]
    assert report.by_status["pending"].net_amount == -50.0
    assert report.by_severity["critical"].net_amount == 550.0
    assert report.by_severity["high"].net_amount == -50.0
    assert report.by_currency["USDC"].transaction_count == 3
    assert [line.outage_id for line in report.lines] == ["p1", "p2", "r1", "z"]


def test_build_settlement_breakdown_excludes_invalid_entries():
    entries = [
        SettlementEntry(outage_id="invalid-type", payment_type="bad", amount=10.0),
        {"outage_id": "missing-amount", "payment_type": "reward"},
        {"outage_id": "nan", "amount": float("nan")},
        object(),
        {"outage_id": "zero", "payment_type": "none", "amount": 50.0},
    ]

    report = SettlementBreakdownService.build(entries)

    assert report.total_records == 5
    assert report.totals.excluded_count == 4
    assert report.totals.no_settlement_count == 1
    assert report.totals.net_amount == 0.0
    assert report.by_status["unknown"].excluded_count == 4
    reasons = {line.outage_id: line.reason_code for line in report.lines}
    assert reasons == {
        "invalid-type": "invalid_payment_type",
        "missing-amount": "invalid_amount",
        "nan": "invalid_amount",
        "": "invalid_record",
        "zero": None,
    }


def test_build_settlement_breakdown_handles_empty_input():
    report = build_settlement_breakdown([])

    assert report.total_records == 0
    assert report.totals.net_amount == 0.0
    assert report.totals.transaction_count == 0
    assert report.by_status == {}
    assert report.by_severity == {}
    assert report.by_currency == {}
    assert report.lines == []
