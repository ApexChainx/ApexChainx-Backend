import hashlib
import hmac
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import require_admin, require_engineer
from app.db.session import get_db
from app.models.payment import PaginatedPayments, PaymentTransaction, PaymentTransitionError
from app.repositories.payment_repository import PaymentRepository
from app.services.audit_log import audit_log

router = APIRouter()

_SEEN_NONCES: dict[str, float] = {}
CALLBACK_NONCE_TTL_SECONDS = 300


def _evict_stale_nonces() -> None:
    """Remove nonces older than the replay window (called on each callback)."""
    cutoff = time.monotonic() - CALLBACK_NONCE_TTL_SECONDS
    stale = [k for k, ts in _SEEN_NONCES.items() if ts < cutoff]
    for k in stale:
        del _SEEN_NONCES[k]


def _is_replay(nonce: str) -> bool:
    """Return True if *nonce* has been seen within the replay window."""
    _evict_stale_nonces()
    if nonce in _SEEN_NONCES:
        return True
    _SEEN_NONCES[nonce] = time.monotonic()
    return False


# BE-027: Schemas for reconciliation history
class ReconciliationHistoryEntry(BaseModel):
    """A single reconciliation history entry."""

    event_type: str
    actor: str | None = None
    previous_status: str | None = None
    new_status: str
    timestamp: str
    details: dict[str, Any] | None = None


class ReconciliationHistoryResponse(BaseModel):
    """Payment reconciliation history response."""

    transaction_id: str
    current_status: str
    history: list[ReconciliationHistoryEntry]


@router.get("/", response_model=PaginatedPayments)
def list_payments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    type: str | None = None,
    outage_id: str | None = None,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    current_user=Depends(require_engineer),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    repo = PaymentRepository(db)
    items, total = repo.list(
        page=page,
        page_size=page_size,
        status=status,
        outage_id=outage_id,
        type=type,
        date_from=date_from,
        date_to=date_to,
    )
    return PaginatedPayments(items=items, total=total, page=page, page_size=page_size)


@router.get("/ping")
def payments_ping():
    return {"message": "payments ok"}


@router.get("/{transaction_id}/history", response_model=list[dict[str, Any]])
def get_payment_history(transaction_id: str, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    repo = PaymentRepository(db)
    if not repo.get(transaction_id):
        raise HTTPException(status_code=404, detail="Payment not found")
    return repo.get_payment_history(transaction_id)


@router.get("/{transaction_id}/reconciliation-history", response_model=ReconciliationHistoryResponse)
def get_payment_reconciliation_history(
    transaction_id: str, current_user=Depends(require_engineer), db: Session = Depends(get_db)
):
    """Get detailed reconciliation history for a payment with timestamps and actor context.

    BE-027: Returns a stable, structured history suitable for frontend drawer or audit screen.
    """
    repo = PaymentRepository(db)
    payment = repo.get(transaction_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    history = repo.get_reconciliation_history(transaction_id)

    return ReconciliationHistoryResponse(
        transaction_id=transaction_id,
        current_status=payment.status,
        history=history,
    )


@router.get("/{transaction_id}", response_model=PaymentTransaction)
def get_payment(transaction_id: str, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    repo = PaymentRepository(db)
    payment = repo.get(transaction_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


class ReconcileRequest(BaseModel):
    status: str


@router.post("/{transaction_id}/reconcile", response_model=PaymentTransaction)
def reconcile_payment(
    transaction_id: str, payload: ReconcileRequest, current_user=Depends(require_admin), db: Session = Depends(get_db)
):
    repo = PaymentRepository(db)
    existing = repo.get(transaction_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Payment not found")

    try:
        payment = repo.reconcile(transaction_id, payload.status)
    except PaymentTransitionError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "current_status": exc.current,
                "requested_status": exc.next_status,
                "allowed_transitions": list(exc.allowed),
            },
        )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # BE-027: Include previous status in audit log for reconciliation history
    audit_log.log(
        "payment_reconciled",
        {
            "id": transaction_id,
            "previous_status": existing.status,
            "status": payload.status,
        },
    )
    return payment


@router.post("/{transaction_id}/retry", response_model=PaymentTransaction)
def retry_payment(transaction_id: str, current_user=Depends(require_engineer), db: Session = Depends(get_db)):
    repo = PaymentRepository(db)
    existing = repo.get(transaction_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Payment not found")
    try:
        payment = repo.retry(transaction_id)
    except PaymentTransitionError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "current_status": exc.current,
                "requested_status": exc.next_status,
                "allowed_transitions": list(exc.allowed),
            },
        )
    if not payment:
        raise HTTPException(status_code=409, detail="Max retries reached")
    audit_log.log("payment_retried", {"id": transaction_id, "retry_count": payment.retry_count})
    return payment


class ProviderCallbackRequest(BaseModel):
    transaction_id: str
    status: str
    provider_ref: str | None = None
    # BE-028: callers must supply a per-request nonce for replay protection.
    # The nonce must be unique within the CALLBACK_NONCE_TTL_SECONDS window.
    nonce: str | None = None


def _verify_callback_signature(
    transaction_id: str,
    status: str,
    nonce: str | None,
    signature: str,
    secret: str,
) -> bool:
    """HMAC-SHA256 verification.

    Canonical message: ``<transaction_id>:<status>:<nonce>``
    Including the nonce in the signed payload binds the signature to this
    specific request so that replaying a captured (signature, payload) pair
    against a different nonce produces a different expected signature.
    """
    message = f"{transaction_id}:{status}:{nonce or ''}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/provider-callback", response_model=PaymentTransaction)
def provider_callback(
    payload: ProviderCallbackRequest,
    x_webhook_signature: str | None = Header(default=None),
    x_callback_nonce: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Inbound callback from a payment provider to update payment status (BE-028).

    Security model:
    - HMAC-SHA256 signature (X-Webhook-Signature) verified when
      PAYMENT_WEBHOOK_SECRET is configured.  The signed message includes the
      nonce so replaying a captured request fails if the nonce changes.
    - Replay protection: the nonce (from X-Callback-Nonce header or the
      ``nonce`` body field) must be unique within a 5-minute window.
      Duplicate nonces are rejected with 409 Conflict.
    - Idempotency: a callback that moves a payment into its current status
      is silently accepted (returns 200 with the unchanged record).
    - Failures and suspicious events are written to the audit log so they
      are reviewable later.
    """
    # --- 1. Resolve nonce (header takes precedence over body field) ----------
    effective_nonce = x_callback_nonce or payload.nonce

    # --- 2. Authenticate signature -------------------------------------------
    secret = settings.PAYMENT_WEBHOOK_SECRET
    if secret:
        if not x_webhook_signature:
            audit_log.log(
                "callback_rejected_missing_signature",
                {"transaction_id": payload.transaction_id, "provider_ref": payload.provider_ref},
            )
            raise HTTPException(status_code=401, detail="Missing webhook signature")

        if not _verify_callback_signature(
            payload.transaction_id,
            payload.status,
            effective_nonce,
            x_webhook_signature,
            secret,
        ):
            audit_log.log(
                "callback_rejected_bad_signature",
                {"transaction_id": payload.transaction_id, "provider_ref": payload.provider_ref},
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if effective_nonce:
        if _is_replay(effective_nonce):
            audit_log.log(
                "callback_rejected_replay",
                {
                    "transaction_id": payload.transaction_id,
                    "nonce": effective_nonce,
                    "provider_ref": payload.provider_ref,
                },
            )
            raise HTTPException(
                status_code=409,
                detail="Duplicate callback nonce – possible replay attack",
            )

    repo = PaymentRepository(db)
    existing = repo.get(payload.transaction_id)
    if not existing:
        audit_log.log(
            "callback_rejected_unknown_payment",
            {"transaction_id": payload.transaction_id, "provider_ref": payload.provider_ref},
        )
        raise HTTPException(status_code=404, detail="Payment not found")

    if existing.status == payload.status:
        return existing

    try:
        updated = repo.reconcile(payload.transaction_id, payload.status)
    except ValueError as exc:
        audit_log.log(
            "callback_rejected_invalid_transition",
            {
                "transaction_id": payload.transaction_id,
                "from_status": existing.status,
                "to_status": payload.status,
                "provider_ref": payload.provider_ref,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=422, detail=str(exc))

    audit_log.log(
        "payment_provider_callback",
        {
            "id": payload.transaction_id,
            "status": payload.status,
            "provider_ref": payload.provider_ref,
            "nonce": effective_nonce,
        },
    )
    return updated
