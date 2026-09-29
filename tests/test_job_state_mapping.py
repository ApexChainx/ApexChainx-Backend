"""Unit tests for the JobState <-> Celery state mapping at the API boundary (#571).

The job API must only expose states this service owns: every Celery state —
including the scheduling states RETRY / RECEIVED / SCHEDULED / REJECTED that a
worker can surface — maps onto a persisted :class:`JobStatus`, and unknown
states map to ``None`` so callers keep the stored status.
"""

import pytest

from app.models.job import JobState, JobStatus


class TestKnownCeleryStates:
    @pytest.mark.parametrize(
        ("celery_state", "expected"),
        [
            ("SUCCESS", JobStatus.SUCCESS),
            ("FAILURE", JobStatus.FAILURE),
            ("REVOKED", JobStatus.REVOKED),
            ("STARTED", JobStatus.STARTED),
            ("PENDING", JobStatus.PENDING),
            # Celery re-enqueued the task — queued again, not running.
            ("RETRY", JobStatus.PENDING),
            # Framework scheduling states between reception and execution.
            ("RECEIVED", JobStatus.PENDING),
            ("SCHEDULED", JobStatus.PENDING),
            # Worker rejected the task (e.g. requeue on worker loss).
            ("REJECTED", JobStatus.FAILURE),
        ],
    )
    def test_every_celery_state_maps(self, celery_state, expected):
        assert JobState.to_job_status(celery_state) is expected

    def test_mapping_is_case_insensitive(self):
        assert JobState.to_job_status("success") is JobStatus.SUCCESS
        assert JobState.to_job_status("Retry") is JobStatus.PENDING


class TestEdgeStates:
    @pytest.mark.parametrize("bad", [None, "", "   "])
    def test_missing_state_returns_none(self, bad):
        assert JobState.to_job_status(bad) is None

    def test_unknown_state_returns_none(self):
        # Not a real Celery state — must not guess a status.
        assert JobState.to_job_status("ZOMBIE") is None

    def test_job_state_enum_covers_all_celery_states(self):
        # The enum itself must enumerate every state it can map. Values are
        # the lower-case Celery state names; names are the upper-case ones.
        documented = {
            "SUCCESS",
            "FAILURE",
            "REVOKED",
            "STARTED",
            "PENDING",
            "RETRY",
            "RECEIVED",
            "SCHEDULED",
            "REJECTED",
        }
        assert {member.name for member in JobState} == documented

    def test_job_status_values_unchanged(self):
        # The persisted enum is part of the API contract.
        assert {s.value for s in JobStatus} == {"pending", "started", "success", "failure", "revoked"}


class TestAPIBoundary:
    def test_sync_endpoint_uses_enum_mapping(self):
        # The endpoint module must no longer carry its own inline state map.
        import inspect

        from app.api.v1.endpoints import jobs

        source = inspect.getsource(jobs._sync_job_status_from_celery)
        assert "state_map" not in source
        assert "JobState.to_job_status" in source

    def test_job_status_cache_stores_mapped_status(self):
        """A job whose Celery state is a scheduling state gets PENDING cached,
        not the raw Celery string."""
        from unittest.mock import MagicMock, patch

        from app.api.v1.endpoints.jobs import _job_status_cache, _sync_job_status_from_celery

        job = MagicMock()
        job.id = "job-1"
        job.status = JobStatus.PENDING
        job.celery_task_id = "task-1"

        with patch("app.api.v1.endpoints.jobs.AsyncResult") as mock_result_cls:
            mock_result_cls.return_value.state = "RECEIVED"
            _job_status_cache._cache.clear() if hasattr(_job_status_cache, "_cache") else None
            result = _sync_job_status_from_celery(MagicMock(), job)

        assert result.status == JobStatus.PENDING
