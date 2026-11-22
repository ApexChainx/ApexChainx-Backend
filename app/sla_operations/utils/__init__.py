"""Utility helpers for the SLA operations workspace.

This package exposes small, dependency-light helpers that can be reused by
workspace services, schemas, and analytics code without pulling in core
application state (database sessions, ORM models, settings, etc.).

Currently exported helpers focus on status normalization for outage, payment,
and webhook status values.
"""

from .statuses import (
    CANONICAL_OUTAGE_STATUSES,
    CANONICAL_PAYMENT_STATUSES,
    CANONICAL_WEBHOOK_STATUSES,
    InvalidStatusError,
    normalize_outage_status,
    normalize_payment_status,
    normalize_status,
    normalize_webhook_status,
)

__all__ = [
    "CANONICAL_OUTAGE_STATUSES",
    "CANONICAL_PAYMENT_STATUSES",
    "CANONICAL_WEBHOOK_STATUSES",
    "InvalidStatusError",
    "normalize_outage_status",
    "normalize_payment_status",
    "normalize_status",
    "normalize_webhook_status",
]