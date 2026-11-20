"""Deterministic payment and reconciliation samples for workspace tests.

Call the helpers to get fresh dictionaries, so tests may safely mutate their
copies without affecting other consumers.
"""

from datetime import UTC, datetime
from typing import Literal, TypedDict


class SettlementPayment(TypedDict):
    id: str
    transaction_hash: str
    type: Literal["reward", "penalty"]
    amount: int
    asset_code: str
    from_address: str
    to_address: str
    status: Literal["pending", "confirmed", "failed"]
    outage_id: str
    sla_result_id: int | None
    created_at: datetime
    confirmed_at: datetime | None
    retry_count: int
    last_retried_at: datetime | None


class ReconciliationSample(TypedDict):
    payment_id: str
    ledger_amount: int
    recorded_amount: int
    matches: bool
    note: str


def settlement_payment_fixtures() -> list[SettlementPayment]:
    """Return reward, penalty, pending, and failed payment examples."""
    return [
        {
            "id": "payment-001",
            "transaction_hash": "stellar-tx-reward-001",
            "type": "reward",
            "amount": 125,
            "asset_code": "USDC",
            "from_address": "SYSTEM_POOL",
            "to_address": "WALLET_OPERATOR_001",
            "status": "confirmed",
            "outage_id": "outage-001",
            "sla_result_id": 101,
            "created_at": datetime(2026, 11, 18, 10, 0, tzinfo=UTC),
            "confirmed_at": datetime(2026, 11, 18, 10, 2, tzinfo=UTC),
            "retry_count": 0,
            "last_retried_at": None,
        },
        {
            "id": "payment-002",
            "transaction_hash": "stellar-tx-penalty-002",
            "type": "penalty",
            "amount": 40,
            "asset_code": "USDC",
            "from_address": "WALLET_PROVIDER_002",
            "to_address": "SYSTEM_POOL",
            "status": "confirmed",
            "outage_id": "outage-002",
            "sla_result_id": 102,
            "created_at": datetime(2026, 11, 18, 11, 0, tzinfo=UTC),
            "confirmed_at": datetime(2026, 11, 18, 11, 3, tzinfo=UTC),
            "retry_count": 0,
            "last_retried_at": None,
        },
        {
            "id": "payment-003",
            "transaction_hash": "pending-payment-003",
            "type": "reward",
            "amount": 0,
            "asset_code": "USDC",
            "from_address": "SYSTEM_POOL",
            "to_address": "WALLET_OPERATOR_003",
            "status": "pending",
            "outage_id": "outage-003",
            "sla_result_id": 103,
            "created_at": datetime(2026, 11, 18, 12, 0, tzinfo=UTC),
            "confirmed_at": None,
            "retry_count": 0,
            "last_retried_at": None,
        },
        {
            "id": "payment-004",
            "transaction_hash": "stellar-tx-failed-004",
            "type": "penalty",
            "amount": 25,
            "asset_code": "USDC",
            "from_address": "WALLET_PROVIDER_004",
            "to_address": "SYSTEM_POOL",
            "status": "failed",
            "outage_id": "outage-004",
            "sla_result_id": 104,
            "created_at": datetime(2026, 11, 18, 13, 0, tzinfo=UTC),
            "confirmed_at": None,
            "retry_count": 1,
            "last_retried_at": datetime(2026, 11, 18, 13, 5, tzinfo=UTC),
        },
    ]


def reconciliation_fixtures() -> list[ReconciliationSample]:
    """Return a matching settlement and a deliberately mismatched ledger row."""
    return [
        {
            "payment_id": "payment-001",
            "ledger_amount": 125,
            "recorded_amount": 125,
            "matches": True,
            "note": "confirmed reward matches the Stellar ledger",
        },
        {
            "payment_id": "payment-002",
            "ledger_amount": 39,
            "recorded_amount": 40,
            "matches": False,
            "note": "intentional one-unit discrepancy for reconciliation tests",
        },
        {
            "payment_id": "payment-003",
            "ledger_amount": 0,
            "recorded_amount": 0,
            "matches": True,
            "note": "pending zero-value reward has no transfer to reconcile",
        },
    ]
