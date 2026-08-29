from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PaymentStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"


# Allowed transitions: from_status -> set of valid to_statuses
VALID_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.pending: frozenset({PaymentStatus.confirmed, PaymentStatus.failed}),
    PaymentStatus.confirmed: frozenset(),
    PaymentStatus.failed: frozenset({PaymentStatus.pending}),
}


class PaymentTransitionError(ValueError):
    """Raised when a payment status transition is not allowed.

    Carries structured data so the API layer can produce a consistent,
    typed 422 response without re-parsing the error message.
    """

    def __init__(self, current: str, next_status: str, allowed: set[str]) -> None:
        self.current = current
        self.next_status = next_status
        self.allowed = allowed
        super().__init__(
            f"Transition from '{current}' to '{next_status}' is not allowed. " f"Allowed: {allowed or 'none'}"
        )


def validate_transition(current: str, next_status: str) -> None:
    """Raise :class:`PaymentTransitionError` if the transition is not allowed.

    This is the single authoritative policy for payment status transitions.
    All code paths – retry, reconcile, callback – MUST call this function
    rather than implementing their own transition logic.
    """
    try:
        current_enum = PaymentStatus(current)
        next_enum = PaymentStatus(next_status)
    except ValueError:
        raise PaymentTransitionError(
            current=current,
            next_status=next_status,
            allowed={s.value for s in VALID_TRANSITIONS.get(PaymentStatus(current), frozenset())}
            if current in PaymentStatus._value2member_map_
            else set(),
        )
    if next_enum not in VALID_TRANSITIONS[current_enum]:
        allowed = {s.value for s in VALID_TRANSITIONS[current_enum]}
        raise PaymentTransitionError(current=current, next_status=next_status, allowed=allowed)


class PaymentTransaction(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "payment-001",
                "transaction_hash": "tx-123abc",
                "type": "reward",
                "amount": 150.0,
                "asset_code": "USDC",
                "from_address": "SYSTEM_POOL",
                "to_address": "OUTAGE_SETTLEMENT",
                "status": "confirmed",
                "outage_id": "outage-001",
                "sla_result_id": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "confirmed_at": "2026-01-01T01:00:00Z",
                "retry_count": 0,
                "last_retried_at": None,
            }
        }
    )

    id: str
    transaction_hash: str
    type: str
    amount: int
    asset_code: str
    from_address: str
    to_address: str
    status: str
    outage_id: str
    sla_result_id: int | None = None
    created_at: datetime
    confirmed_at: datetime | None = None
    retry_count: int = 0
    last_retried_at: datetime | None = None


class PaginatedPayments(BaseModel):
    items: list[PaymentTransaction]
    total: int
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
