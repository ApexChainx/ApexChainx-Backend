import threading
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.lock import ConcurrencyLockError, _lock_id_from_key, advisory_lock
from app.db.session import SessionLocal

pytestmark = pytest.mark.skipif("sqlite" in settings.DATABASE_URL, reason="Requires PostgreSQL advisory locks")


def _session_or_skip():
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except OperationalError as exc:
        session.close()
        pytest.skip(f"Requires a reachable PostgreSQL database: {exc}")
    return session


def test_advisory_lock_waits_for_contended_lock():
    holder = _session_or_skip()
    waiter = SessionLocal()
    lock_key = f"test:advisory-lock:{time.monotonic_ns()}"
    entered = threading.Event()
    failure = []

    try:
        holder.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": _lock_id_from_key(lock_key)},
        )

        def acquire_lock():
            try:
                with advisory_lock(waiter, lock_key, timeout_seconds=1.0):
                    entered.set()
            except Exception as exc:
                failure.append(exc)

        thread = threading.Thread(target=acquire_lock)
        thread.start()
        assert not entered.wait(0.15)
        holder.rollback()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert failure == []
        assert entered.is_set()
    finally:
        waiter.rollback()
        waiter.close()
        holder.rollback()
        holder.close()


def test_advisory_lock_raises_after_timeout():
    holder = _session_or_skip()
    waiter = SessionLocal()
    lock_key = f"test:advisory-lock-timeout:{time.monotonic_ns()}"

    try:
        holder.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
            {"lock_id": _lock_id_from_key(lock_key)},
        )
        started = time.monotonic()
        with pytest.raises(ConcurrencyLockError):
            with advisory_lock(waiter, lock_key, timeout_seconds=0.2):
                pass
        assert time.monotonic() - started >= 0.18
    finally:
        waiter.rollback()
        waiter.close()
        holder.rollback()
        holder.close()
