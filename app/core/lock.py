"""Distributed locking utilities for concurrency protection.

Provides advisory lock mechanisms using PostgreSQL's pg_advisory_xact_lock
to prevent concurrent execution of critical operations like SLA resolution
and recomputation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session


class ConcurrencyLockError(Exception):
    """Raised when a lock cannot be acquired."""

    pass


def _lock_id_from_key(key: str) -> int:
    """Convert a string key to a 64-bit integer for PostgreSQL advisory locks.

    Uses SHA-256 to generate a deterministic hash, then takes the first 8 bytes.
    """
    hash_bytes = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(hash_bytes[:8], byteorder="big", signed=False)


@contextmanager
def advisory_lock(db: Session, lock_key: str, timeout_seconds: float = 5.0) -> Generator[None, None, None]:
    """Acquire a PostgreSQL advisory lock for the duration of a transaction.

    This is a transaction-scoped lock that is automatically released when the
    transaction commits or rolls back.

    Args:
        db: SQLAlchemy session
        lock_key: Unique string identifier for the lock (e.g., "resolve:outage_123")
        timeout_seconds: Maximum time to wait for the lock (not directly enforced by PG,
                        but we can check before acquiring)

    Yields:
        None

    Raises:
        ConcurrencyLockError: If the lock cannot be acquired

    Example:
        with advisory_lock(db, f"resolve:{outage_id}"):
            # Critical section - only one transaction can execute this at a time
            outage = repo.resolve(outage_id, mttr_minutes)
            db.commit()
    """
    lock_id = _lock_id_from_key(lock_key)

    # Try to acquire the lock (non-blocking first check)
    result = db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    acquired = result.scalar()

    if not acquired:
        raise ConcurrencyLockError(f"Could not acquire lock for '{lock_key}'. Another operation is in progress.")

    try:
        yield
    except Exception:
        # Lock is automatically released on transaction rollback
        raise


@contextmanager
def advisory_lock_nowait(db: Session, lock_key: str) -> Generator[None, None, None]:
    """Acquire a PostgreSQL advisory lock without waiting.

    Immediately fails if the lock is already held by another transaction.

    Args:
        db: SQLAlchemy session
        lock_key: Unique string identifier for the lock

    Yields:
        None

    Raises:
        ConcurrencyLockError: If the lock is already held

    Example:
        with advisory_lock_nowait(db, f"recompute:{outage_id}"):
            # Critical section
            stored_sla = sla_repo.create_if_changed(sla)
            db.commit()
    """
    lock_id = _lock_id_from_key(lock_key)

    result = db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    acquired = result.scalar()

    if not acquired:
        raise ConcurrencyLockError(f"Operation for '{lock_key}' is already in progress. Please retry later.")

    try:
        yield
    except Exception:
        raise
