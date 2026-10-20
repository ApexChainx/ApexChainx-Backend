"""Health report for the block poller (issue #730).

The poller has one failure mode that matters: a backlog of payments that
never drains. A payment that was submitted an hour ago and is still
"pending" means the settlement never happened, and nothing in the API
answers "is the poller keeping up?".

The report therefore answers three questions:

* can the poller's own bookkeeping be read at all?
* how many payments are still waiting?
* how old is the oldest one?

The report exposes aggregate numbers only -- no payment ids, no transaction
hashes -- so it can be scraped by a monitoring system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.orm.payment import PaymentTransactionORM

# A pending payment that is older than this is a backlog that is not
# draining, which is the failure mode the poller exists to prevent.
DEFAULT_MAX_PENDING_AGE_SECONDS = 300

# The poller only ever looks at payments that have not been settled yet.
POLLABLE_PAYMENT_STATUSES = frozenset({"pending"})


@dataclass
class ComponentStatus:
    """The status of one component of the poller."""

    component: str
    status: str  # ok, warn, down
    details: dict


def _pending_filter():
    """The filter that selects the payments the poller still has to settle."""
    return and_(
        PaymentTransactionORM.status.in_(sorted(POLLABLE_PAYMENT_STATUSES)),
        PaymentTransactionORM.confirmed_at.is_(None),
    )


def _age_seconds(oldest: datetime | None) -> float | None:
    """Return how long ago ``oldest`` was, in seconds."""
    if oldest is None:
        return None
    if oldest.tzinfo is None:
        # A database that hands back naive timestamps is treated as UTC.
        oldest = oldest.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - oldest).total_seconds())


def build_block_poller_report(
    db: Session,
    *,
    max_pending_age_seconds: int = DEFAULT_MAX_PENDING_AGE_SECONDS,
) -> dict:
    """Build the health report of the block poller.

    Policy: a database that cannot be read is "down"; a backlog that is not
    draining is "warn", so a partially degraded poller does not take the
    instance out of the load balancer rotation.
    """
    components: list[ComponentStatus] = []

    try:
        pending = (db.query(func.count()).select_from(PaymentTransactionORM).filter(_pending_filter()).scalar()) or 0
        oldest = db.query(func.min(PaymentTransactionORM.created_at)).filter(_pending_filter()).scalar()
    except SQLAlchemyError as exc:
        components.append(ComponentStatus("block_poller.database", "down", {"error": str(exc)}))
        return {
            "status": "down",
            "components": {c.component: {"status": c.status, **c.details} for c in components},
        }

    age_seconds = _age_seconds(oldest)
    backlog_status = "ok"
    if age_seconds is not None and age_seconds > max_pending_age_seconds:
        backlog_status = "warn"

    components.append(
        ComponentStatus(
            "block_poller.backlog",
            backlog_status,
            {
                "pending_payments": int(pending),
                "oldest_pending_age_seconds": round(age_seconds, 2) if age_seconds is not None else None,
                "max_pending_age_seconds": max_pending_age_seconds,
            },
        )
    )

    overall = "warn" if any(c.status == "warn" for c in components) else "ok"
    return {
        "status": overall,
        "components": {c.component: {"status": c.status, **c.details} for c in components},
    }
