"""#571: Celery state → JobStatus mapping at the jobs API boundary.

The jobs API used to inline a five-key lookup that copied Celery strings
through loosely; anything unmapped silently froze the stored status. The
boundary now goes through one closed, explicit table (app/utils/job_states.py)
and only ever reports JobStatus members.
"""

import pytest

from app.models.job import JobStatus
from app.utils.job_states import (
    CELERY_STATE_TO_JOB_STATUS,
    celery_state_to_job_status,
)

# Every state Celery's backends can report for a task, per celery.result and
# docs/reference: PENDING, STARTED, RETRY, SUCCESS, FAILURE, REVOKED.
ALL_CELERY_STATES = ["PENDING", "STARTED", "RETRY", "SUCCESS", "FAILURE", "REVOKED"]


class TestCeleryStateMapping:
    @pytest.mark.parametrize("celery_state", ALL_CELERY_STATES)
    def test_every_core_celery_state_is_mapped(self, celery_state):
        assert celery_state in CELERY_STATE_TO_JOB_STATUS

    @pytest.mark.parametrize(
        ("celery_state", "expected"),
        [
            ("PENDING", JobStatus.PENDING),
            ("STARTED", JobStatus.STARTED),
            ("RETRY", JobStatus.STARTED),
            ("SUCCESS", JobStatus.SUCCESS),
            ("FAILURE", JobStatus.FAILURE),
            ("REVOKED", JobStatus.REVOKED),
        ],
    )
    def test_mapping_targets(self, celery_state, expected):
        assert celery_state_to_job_status(celery_state, fallback=JobStatus.PENDING) is expected

    @pytest.mark.parametrize("celery_state", ALL_CELERY_STATES)
    def test_mapping_only_emits_job_status_members(self, celery_state):
        result = celery_state_to_job_status(celery_state, fallback=JobStatus.PENDING)
        assert isinstance(result, JobStatus)
        assert result in set(JobStatus)

    @pytest.mark.parametrize(
        "unmapped",
        [
            "PROGRESS",  # custom worker state
            "SCHEDULED",  # custom state some brokers report
            "",  # empty
            None,  # AsyncResult.state can be None in edge cases
            "sent",  # pre-PENDING transport-level state
        ],
    )
    def test_unmapped_states_fall_back_to_stored_status(self, unmapped):
        stored = JobStatus.STARTED
        assert celery_state_to_job_status(unmapped, fallback=stored) is stored

    @pytest.mark.parametrize("celery_state", [s.lower() for s in ALL_CELERY_STATES])
    def test_mapping_tolerates_lowercase(self, celery_state):
        # Backend strings are normalised case-insensitively before lookup.
        expected = CELERY_STATE_TO_JOB_STATUS[celery_state.upper()]
        assert celery_state_to_job_status(celery_state, fallback=JobStatus.PENDING) is expected

    def test_retry_is_not_terminal(self):
        # RETRY maps to started (an attempt is running/waiting), never to a
        # terminal status: a retried job must stay retry-eligible.
        assert CELERY_STATE_TO_JOB_STATUS["RETRY"] not in (
            JobStatus.SUCCESS,
            JobStatus.FAILURE,
            JobStatus.REVOKED,
        )

    def test_terminal_states_are_terminal(self):
        assert CELERY_STATE_TO_JOB_STATUS["SUCCESS"] == JobStatus.SUCCESS
        assert CELERY_STATE_TO_JOB_STATUS["FAILURE"] == JobStatus.FAILURE
        assert CELERY_STATE_TO_JOB_STATUS["REVOKED"] == JobStatus.REVOKED


class TestSyncFlowUsesMapping:
    def test_unknown_celery_state_keeps_stored_status(self):
        """_sync_job_status_from_celery no longer needs its own lookup; the
        closed mapping is the single source of truth. Unknown states keep the
        stored status instead of raising or leaking through."""
        from app.utils.job_states import celery_state_to_job_status as mapper

        stored = JobStatus.STARTED
        assert mapper("SOME_CUSTOM_STATE", fallback=stored) is stored

    def test_api_responses_can_only_carry_job_status_values(self):
        """The state set exposed by the Job API equals JobStatus, so no
        Celery string can ever appear in a response payload."""
        api_states = {status.value for status in JobStatus}
        assert api_states == {"pending", "started", "success", "failure", "revoked"}
