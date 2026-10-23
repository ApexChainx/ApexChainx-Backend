"""Issue #575 — SLA recompute enqueue must not create concurrent duplicate jobs.

``enqueue_sla_computation`` and ``enqueue_bulk_sla_computation`` in
``app/tasks/sla_tasks.py`` used to enqueue a fresh Celery task on every call,
with no guard keyed by the SLA being recomputed. Two admins clicking
"recompute" for the same device/period at nearly the same time — or a beat
tick racing a manual run — enqueued the same computation twice, doubling
compute and racing on the unique ``sla_result_id`` (related IntegrityError-500
issue).

These tests patch the Celery task's ``apply_async`` so no real SLA
computation runs; they only exercise the dedup guard itself: a job for the
same (device_id, period) — or the same bulk device set + period — that is
still in flight must be returned instead of enqueueing a second task.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.job import Job, JobStatus, JobType
from app.tasks.sla_tasks import (
    celery_app,
    enqueue_bulk_sla_computation,
    enqueue_sla_computation,
)

pytestmark = pytest.mark.skipif(
    "sqlite" in settings.DATABASE_URL, reason="Requires PostgreSQL advisory locks (see app/core/lock.py)"
)


def _session_or_skip():
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except OperationalError as exc:
        session.close()
        pytest.skip(f"Requires a reachable PostgreSQL database: {exc}")
    return session


@pytest.fixture
def db():
    session = _session_or_skip()
    try:
        yield session
    finally:
        session.rollback()
        session.query(Job).delete()
        session.commit()
        session.close()


def _fake_result(task_id: str) -> SimpleNamespace:
    """Stand-in for the AsyncResult apply_async() returns."""
    return SimpleNamespace(id=task_id)


def test_enqueue_sla_computation_dedupes_concurrent_requests(db):
    """Two enqueues for the same (device_id, period) must produce one job."""
    with patch("app.tasks.sla_tasks.compute_sla_for_device.apply_async") as mock_apply_async:
        mock_apply_async.return_value = _fake_result("task-1")

        first = enqueue_sla_computation(db, device_id="device-1", period="2026-09")
        second = enqueue_sla_computation(db, device_id="device-1", period="2026-09")

    assert second.id == first.id
    assert mock_apply_async.call_count == 1
    assert db.query(Job).filter(Job.job_type == JobType.SLA_COMPUTATION).count() == 1


def test_enqueue_sla_computation_allows_different_period(db):
    """A different period is a different SLA recompute — not deduped."""
    with patch("app.tasks.sla_tasks.compute_sla_for_device.apply_async") as mock_apply_async:
        mock_apply_async.side_effect = [_fake_result("task-1"), _fake_result("task-2")]

        first = enqueue_sla_computation(db, device_id="device-1", period="2026-09")
        second = enqueue_sla_computation(db, device_id="device-1", period="2026-10")

    assert second.id != first.id
    assert mock_apply_async.call_count == 2


def test_enqueue_sla_computation_allows_new_job_once_previous_finished(db):
    """Once the in-flight job reaches a terminal Celery state, a new request may enqueue again."""
    with patch("app.tasks.sla_tasks.compute_sla_for_device.apply_async") as mock_apply_async:
        mock_apply_async.side_effect = [_fake_result("task-done"), _fake_result("task-new")]

        first = enqueue_sla_computation(db, device_id="device-2", period="2026-09")
        # Simulate the worker having actually finished this task.
        celery_app.backend.store_result(first.celery_task_id, None, "SUCCESS")

        second = enqueue_sla_computation(db, device_id="device-2", period="2026-09")

    assert second.id != first.id
    assert mock_apply_async.call_count == 2


def test_enqueue_bulk_sla_computation_dedupes_same_device_set(db):
    """Two bulk enqueues for the same device set + period must produce one job,
    regardless of the order the device ids are given in."""
    with patch("app.tasks.sla_tasks.compute_bulk_sla.apply_async") as mock_apply_async:
        mock_apply_async.return_value = _fake_result("bulk-task-1")

        first = enqueue_bulk_sla_computation(db, device_ids=["device-a", "device-b"], period="2026-09")
        second = enqueue_bulk_sla_computation(db, device_ids=["device-b", "device-a"], period="2026-09")

    assert second.id == first.id
    assert mock_apply_async.call_count == 1
    assert db.query(Job).filter(Job.job_type == JobType.BULK_SLA_COMPUTATION).count() == 1


def test_enqueue_sla_computation_job_status_defaults_to_pending(db):
    """Sanity check the dedup guard's own precondition: a freshly created job is PENDING."""
    with patch("app.tasks.sla_tasks.compute_sla_for_device.apply_async") as mock_apply_async:
        mock_apply_async.return_value = _fake_result("task-status-check")
        job = enqueue_sla_computation(db, device_id="device-3", period="2026-09")

    assert job.status == JobStatus.PENDING
