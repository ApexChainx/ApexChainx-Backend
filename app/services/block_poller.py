"""BlockPoller: reconciles submitted payments against the Stellar network.

A payment row is written the moment an SLA payout is decided, but the
settlement only becomes real once the transaction is confirmed on-chain.
``BlockPoller`` is the component that closes that gap: it looks at the
payments that are still pending and asks the network about them.

Distributed tracing (issue #731)
--------------------------------
Every poll cycle runs inside an OpenTelemetry span, so the time the poller
spends talking to the network is visible next to the time the API spends on
the same request. The span carries the poll limit as an attribute, and the
active span is exported as a ``traceparent`` so an outbound request can be
linked to the poll that caused it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.tracing import get_current_traceparent, traced
from app.models.orm.payment import PaymentTransactionORM

logger = logging.getLogger(__name__)

# The poller only ever looks at payments that have not been settled yet.
POLLABLE_PAYMENT_STATUSES = frozenset({"pending"})

# How many payments one poll cycle looks at.
DEFAULT_POLL_LIMIT = 50

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

    @traced("block_poller.pending_payments")
    def pending_payments(self, limit: int = DEFAULT_POLL_LIMIT) -> list[PaymentTransactionORM]:
        """Return the oldest payments that are still awaiting confirmation."""
        page_size = max(1, min(int(limit), 100))
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

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def mark_confirmed(self, payment: PaymentTransactionORM) -> PaymentTransactionORM:
        """Mark ``payment`` as confirmed on-chain."""
        payment.status = NETWORK_STATUS_CONFIRMED
        payment.confirmed_at = datetime.now(UTC)
        self._db.commit()
        self._db.refresh(payment)
        return payment

    def mark_failed(self, payment: PaymentTransactionORM, reason: str) -> PaymentTransactionORM:
        """Mark ``payment`` as failed after the network rejected it."""
        payment.status = NETWORK_STATUS_FAILED
        self._db.commit()
        self._db.refresh(payment)
        logger.warning("Block poller marked payment %s as failed: %s", payment.id, reason)
        return payment

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def network_status(self, transaction_hash: str) -> str:
        """Ask the network about ``transaction_hash``.

        Returns:
            One of ``confirmed``, ``failed`` or ``pending``.

        Raises:
            BlockPollerUnavailableError: when there is no network client.
            BlockPollerNetworkError: when the network could not be reached or
                answered with something the poller does not understand.
        """
        if self._horizon is None:
            raise BlockPollerUnavailableError("no network client is configured for the block poller")

        try:
            response = self._horizon.transaction(transaction_hash)
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

    def traceparent(self) -> str | None:
        """Return the trace context of the active poll, if there is one.

        An outbound request that carries this header is linked to the poll
        cycle that caused it.
        """
        return get_current_traceparent()

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    @traced("block_poller.poll")
    def poll_once(self, limit: int = DEFAULT_POLL_LIMIT) -> PollReport:
        """Poll the network for the pending payments and reconcile what it can.

        A single unreachable transaction never aborts the cycle: it is
        counted as skipped and the reason is reported, so one bad row cannot
        keep the remaining payments from being settled.
        """
        span = trace.get_current_span()
        if span is not None:
            span.set_attribute("block_poller.limit", int(limit))

        report = PollReport()
        for payment in self.pending_payments(limit=limit):
            report.checked += 1
            try:
                status = self.network_status(payment.transaction_hash)
            except BlockPollerError as exc:
                report.skipped += 1
                report.errors.append(str(exc))
                logger.warning("Block poller could not check payment %s: %s", payment.id, exc)
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
