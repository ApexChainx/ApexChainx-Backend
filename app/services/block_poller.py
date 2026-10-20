"""BlockPoller: reconciles submitted payments against the Stellar network.

A payment row is written the moment an SLA payout is decided, but the
settlement only becomes real once the transaction is confirmed on-chain.
``BlockPoller`` is the component that closes that gap: it looks at the
payments that are still pending and asks the network about them.

Hardening (issue #732)
----------------------
Everything that reaches the database from outside the process is validated
against an allowlist first (see :mod:`app.services.block_poller_security`).
A transaction hash that is not 64 lowercase hexadecimal characters, or a
status outside the known set, is rejected before it can be used as a filter
value.  The statements themselves only use bound parameters, so even a value
that slipped through could not change the shape of the query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.orm.payment import PaymentTransactionORM
from app.services.block_poller_security import (
    POLLABLE_PAYMENT_STATUSES,
    validate_page_size,
    validate_payment_status,
    validate_transaction_hash,
)
from app.utils.logging import get_structured_logger

logger = get_structured_logger(__name__)

# The statuses the network can report for a transaction.
NETWORK_STATUS_CONFIRMED = "confirmed"
NETWORK_STATUS_FAILED = "failed"
NETWORK_STATUS_PENDING = "pending"


class BlockPollerError(RuntimeError):
    """Base class for the errors the poller raises."""


class BlockPollerUnavailableError(BlockPollerError):
    """No network client is configured, so the poller cannot ask anything."""


class BlockPollerNetworkError(BlockPollerError):
    """The network could not be reached, or answered with something unusable."""


@dataclass
class PollReport:
    """The outcome of a single poll cycle."""

    checked: int = 0
    confirmed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the report as a JSON-serialisable dictionary."""
        return {
            "checked": self.checked,
            "confirmed": self.confirmed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": list(self.errors),
        }


class BlockPoller:
    """Polls the Stellar network for the confirmation of pending payments."""

    def __init__(self, db: Session, horizon_client: Any | None = None) -> None:
        self._db = db
        self._horizon = horizon_client

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def pending_payments(self, limit: int = 50) -> list[PaymentTransactionORM]:
        """Return the oldest payments that are still awaiting confirmation.

        The status filter uses a fixed set of values, and the transaction
        identifiers are bound as parameters -- nothing that reaches the
        statement is built by string concatenation.
        """
        page_size = validate_page_size(limit)
        return (
            self._db.query(PaymentTransactionORM)
            .filter(
                and_(
                    PaymentTransactionORM.status.in_(sorted(POLLABLE_PAYMENT_STATUSES)),
                    PaymentTransactionORM.confirmed_at.is_(None),
                )
            )
            .order_by(PaymentTransactionORM.created_at.asc())
            .limit(page_size)
            .all()
        )

    def payment_by_hash(self, transaction_hash: str) -> PaymentTransactionORM | None:
        """Return the payment with ``transaction_hash``, if there is one.

        Raises:
            UnsafeIdentifierError: when the hash is not well formed.
        """
        validated = validate_transaction_hash(transaction_hash)
        return self._db.query(PaymentTransactionORM).filter(PaymentTransactionORM.transaction_hash == validated).first()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def mark_confirmed(self, payment: PaymentTransactionORM) -> PaymentTransactionORM:
        """Mark ``payment`` as confirmed on-chain."""
        payment.status = validate_payment_status(NETWORK_STATUS_CONFIRMED)
        payment.confirmed_at = datetime.now(UTC)
        self._db.commit()
        self._db.refresh(payment)
        return payment

    def mark_failed(self, payment: PaymentTransactionORM, reason: str) -> PaymentTransactionORM:
        """Mark ``payment`` as failed after the network rejected it."""
        payment.status = validate_payment_status(NETWORK_STATUS_FAILED)
        self._db.commit()
        self._db.refresh(payment)
        logger.warning(
            "block_poller_payment_failed",
            event_name="block_poller_payment_failed",
            payment_id=payment.id,
            reason=reason,
        )
        return payment

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def network_status(self, transaction_hash: str) -> str:
        """Ask the network about ``transaction_hash``.

        Returns:
            One of ``confirmed``, ``failed`` or ``pending``.

        Raises:
            UnsafeIdentifierError: when the hash is not well formed.
            BlockPollerUnavailableError: when there is no network client.
            BlockPollerNetworkError: when the network could not be reached or
                answered with something the poller does not understand.
        """
        validated = validate_transaction_hash(transaction_hash)
        if self._horizon is None:
            raise BlockPollerUnavailableError("no network client is configured for the block poller")

        try:
            response = self._horizon.transaction(validated)
        except Exception as exc:  # the network client may raise anything
            raise BlockPollerNetworkError(f"the network could not be reached: {exc}") from exc

        if response is None:
            raise BlockPollerNetworkError("the network returned no record for the transaction")

        successful = response.get("successful") if hasattr(response, "get") else None
        if successful is True:
            return NETWORK_STATUS_CONFIRMED
        if successful is False:
            return NETWORK_STATUS_FAILED
        return NETWORK_STATUS_PENDING

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def poll_once(self, limit: int = 50) -> PollReport:
        """Poll the network for the pending payments and reconcile what it can.

        A single unreachable transaction never aborts the cycle: it is counted
        as skipped and the reason is reported, so one bad row cannot keep the
        remaining payments from being settled.
        """
        report = PollReport()
        for payment in self.pending_payments(limit=limit):
            report.checked += 1
            try:
                status = self.network_status(payment.transaction_hash)
            except BlockPollerError as exc:
                report.skipped += 1
                report.errors.append(str(exc))
                logger.warning(
                    "block_poller_check_failed",
                    event_name="block_poller_check_failed",
                    payment_id=payment.id,
                    error=str(exc),
                )
                continue

            if status == NETWORK_STATUS_CONFIRMED:
                self.mark_confirmed(payment)
                report.confirmed += 1
            elif status == NETWORK_STATUS_FAILED:
                self.mark_failed(payment, "the network reported a failed transaction")
                report.failed += 1
            else:
                report.skipped += 1
        return report
