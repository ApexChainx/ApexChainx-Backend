"""BlockPoller: reconciles submitted payments against the Stellar network.

A payment row is written the moment an SLA payout is decided, but the
settlement only becomes real once the transaction is confirmed on-chain.
``BlockPoller`` is the component that closes that gap: it looks at the
payments that are still pending and asks the network about them.

The backlog is read with a cursor rather than with OFFSET/LIMIT, because the
rows the poller walks are exactly the rows that change under it -- a
payment that gets confirmed shifts every later row (issue #733).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.orm.payment import PaymentTransactionORM
from app.services.block_poller_pagination import (
    DEFAULT_PAGE_SIZE,
    PollerPage,
    clamp_page_size,
    decode_payment_cursor,
    encode_payment_cursor,
)

# The statuses the network can report for a transaction.
NETWORK_STATUS_CONFIRMED = "confirmed"
NETWORK_STATUS_FAILED = "failed"
NETWORK_STATUS_PENDING = "pending"

# The poller only ever looks at payments that have not been settled yet.
POLLABLE_PAYMENT_STATUSES = frozenset({"pending"})


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

    def pending_page(self, cursor: str | None = None, limit: int = DEFAULT_PAGE_SIZE) -> PollerPage:
        """Return one page of the pending payments, oldest first.

        The page is stable while the backlog moves: the next page starts
        after the ``(created_at, id)`` key of the last row of this one, so a
        payment that is confirmed halfway through the backlog is neither
        looked at twice nor skipped.
        """
        page_size = clamp_page_size(limit)

        query = self._db.query(PaymentTransactionORM).filter(
            and_(
                PaymentTransactionORM.status.in_(sorted(POLLABLE_PAYMENT_STATUSES)),
                PaymentTransactionORM.confirmed_at.is_(None),
            )
        )

        decoded = decode_payment_cursor(cursor)
        if decoded is not None:
            payment_id, created_at = decoded
            query = query.filter(
                or_(
                    PaymentTransactionORM.created_at < created_at,
                    and_(
                        PaymentTransactionORM.created_at == created_at,
                        PaymentTransactionORM.id < payment_id,
                    ),
                )
            )

        # One extra row tells us whether there is another page without a
        # second count query.
        rows = (
            query.order_by(PaymentTransactionORM.created_at.asc(), PaymentTransactionORM.id.asc())
            .limit(page_size + 1)
            .all()
        )
        has_more = len(rows) > page_size
        page_rows = rows[:page_size]

        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = encode_payment_cursor(str(last.id), last.created_at)

        return PollerPage(payments=page_rows, next_cursor=next_cursor, has_more=has_more)

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

    # ------------------------------------------------------------------
    # Poll cycle
    # ------------------------------------------------------------------

    def poll_once(self, limit: int = DEFAULT_PAGE_SIZE) -> PollReport:
        """Poll the network for one page of pending payments and reconcile them.

        A single unreachable transaction never aborts the cycle: it is
        counted as skipped and the reason is reported, so one bad row cannot
        keep the remaining payments from being settled.
        """
        return self.poll_page(self.pending_page(limit=limit).payments)

    def poll_all(self, max_pages: int = 10, limit: int = DEFAULT_PAGE_SIZE) -> PollReport:
        """Walk the whole backlog, one cursor page at a time.

        The walk stops at ``max_pages`` so a backlog that never shrinks --
        because the network keeps answering "pending" -- cannot keep the
        worker busy forever.
        """
        report = PollReport()
        cursor: str | None = None
        for _ in range(max(1, max_pages)):
            page = self.pending_page(cursor=cursor, limit=limit)
            if not page.payments:
                break
            page_report = self.poll_page(page.payments)
            report.checked += page_report.checked
            report.confirmed += page_report.confirmed
            report.failed += page_report.failed
            report.skipped += page_report.skipped
            report.errors.extend(page_report.errors)
            if not page.has_more or page.next_cursor is None:
                break
            cursor = page.next_cursor
        return report

    def poll_page(self, payments: list[PaymentTransactionORM]) -> PollReport:
        """Reconcile the given payments and report what happened."""
        report = PollReport()
        for payment in payments:
            report.checked += 1
            try:
                status = self.network_status(payment.transaction_hash)
            except BlockPollerError as exc:
                report.skipped += 1
                report.errors.append(str(exc))
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
