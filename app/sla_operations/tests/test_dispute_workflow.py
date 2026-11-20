"""Tests for the dispute-workflow workspace schemas (#851)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.sla_operations.schemas.dispute_workflow import (
    TERMINAL_STATES,
    DisputeActorRole,
    DisputeState,
    DisputeTransition,
    DisputeTransitionAction,
    DisputeTransitionRequest,
    DisputeWorkflowState,
    DisputeWorkflowSummary,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def request_payload(**overrides):
    payload = {
        "action": DisputeTransitionAction.START_REVIEW,
        "expected_state": DisputeState.PENDING,
        "actor": "engineer-1",
        "actor_role": DisputeActorRole.ENGINEER,
        "requested_at": NOW,
    }
    payload.update(overrides)
    return payload


def transition_payload(**overrides):
    payload = {
        "action": DisputeTransitionAction.START_REVIEW,
        "from_state": DisputeState.PENDING,
        "to_state": DisputeState.IN_REVIEW,
        "actor": "engineer-1",
        "actor_role": DisputeActorRole.ENGINEER,
        "occurred_at": NOW,
    }
    payload.update(overrides)
    return payload


def workflow_state_payload(**overrides):
    payload = {
        "dispute_id": uuid4(),
        "sla_result_id": 42,
        "state": DisputeState.IN_REVIEW,
        "flagged_by": "engineer-1",
        "flagged_at": NOW - timedelta(hours=1),
        "updated_at": NOW,
        "transition_count": 1,
        "latest_transition": transition_payload(),
    }
    payload.update(overrides)
    return payload


def summary_payload(**overrides):
    payload = {
        "period_start": NOW - timedelta(days=30),
        "period_end": NOW,
        "total_disputes": 3,
        "counts_by_state": {
            DisputeState.PENDING: 1,
            DisputeState.IN_REVIEW: 1,
            DisputeState.RESOLVED: 1,
        },
        "open_disputes": 2,
        "average_resolution_minutes": 12.5,
    }
    payload.update(overrides)
    return payload


def test_transition_request_accepts_enum_string_values():
    request = DisputeTransitionRequest.model_validate(
        request_payload(action="start_review", expected_state="pending")
    )

    assert request.action is DisputeTransitionAction.START_REVIEW
    assert request.actor_role is DisputeActorRole.ENGINEER


@pytest.mark.parametrize("action", [DisputeTransitionAction.RESOLVE, DisputeTransitionAction.REJECT])
def test_transition_request_requires_reason_for_final_actions(action):
    with pytest.raises(ValidationError, match="reason"):
        DisputeTransitionRequest.model_validate(request_payload(action=action))

    accepted = DisputeTransitionRequest.model_validate(
        request_payload(action=action, reason="Rechecked the SLA inputs thoroughly")
    )
    assert accepted.reason == "Rechecked the SLA inputs thoroughly"


@pytest.mark.parametrize("field", ["requested_at"])
def test_transition_request_rejects_naive_timestamps(field):
    with pytest.raises(ValidationError, match="timezone-aware"):
        DisputeTransitionRequest.model_validate(request_payload(**{field: NOW.replace(tzinfo=None)}))


@pytest.mark.parametrize(
    "action,from_state,to_state,reason",
    [
        (DisputeTransitionAction.START_REVIEW, DisputeState.PENDING, DisputeState.IN_REVIEW, None),
        (DisputeTransitionAction.RETURN_TO_PENDING, DisputeState.IN_REVIEW, DisputeState.PENDING, None),
        (
            DisputeTransitionAction.RESOLVE,
            DisputeState.IN_REVIEW,
            DisputeState.RESOLVED,
            "SLA recomputation confirmed the result",
        ),
        (
            DisputeTransitionAction.REJECT,
            DisputeState.IN_REVIEW,
            DisputeState.REJECTED,
            "Insufficient evidence for the dispute",
        ),
        (DisputeTransitionAction.WITHDRAW, DisputeState.PENDING, DisputeState.WITHDRAWN, None),
        (DisputeTransitionAction.REOPEN, DisputeState.RESOLVED, DisputeState.IN_REVIEW, None),
    ],
)
def test_allowed_transitions_validate(action, from_state, to_state, reason):
    transition = DisputeTransition.model_validate(
        transition_payload(action=action, from_state=from_state, to_state=to_state, reason=reason)
    )

    assert transition.to_state is to_state


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": DisputeTransitionAction.START_REVIEW, "from_state": DisputeState.IN_REVIEW},
        {
            "action": DisputeTransitionAction.RESOLVE,
            "from_state": DisputeState.IN_REVIEW,
            "to_state": DisputeState.REJECTED,
            "reason": "Rechecked the SLA inputs thoroughly",
        },
        {"action": DisputeTransitionAction.REOPEN, "from_state": DisputeState.WITHDRAWN},
        {"action": DisputeTransitionAction.RESOLVE, "to_state": DisputeState.RESOLVED},
        {"action": "unknown_action"},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"reason": "short"},
    ],
)
def test_invalid_transitions_are_rejected(overrides):
    with pytest.raises(ValidationError):
        DisputeTransition.model_validate(transition_payload(**overrides))


def test_workflow_state_validates_history_and_counts():
    state = DisputeWorkflowState.model_validate(workflow_state_payload())

    assert state.state is DisputeState.IN_REVIEW
    assert state.transition_count == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"sla_result_id": 0},
        {"transition_count": -1},
        {"updated_at": NOW - timedelta(hours=2)},
        {"flagged_at": NOW.replace(tzinfo=None)},
        {
            "latest_transition": transition_payload(
                action=DisputeTransitionAction.RESOLVE,
                from_state=DisputeState.IN_REVIEW,
                to_state=DisputeState.RESOLVED,
                reason="Rechecked the SLA inputs thoroughly",
            )
        },
    ],
)
def test_workflow_state_rejects_inconsistent_payloads(overrides):
    with pytest.raises(ValidationError):
        DisputeWorkflowState.model_validate(workflow_state_payload(**overrides))


def test_workflow_summary_fills_missing_state_counts():
    payload = summary_payload(counts_by_state={DisputeState.PENDING: 2}, total_disputes=2, open_disputes=2)

    summary = DisputeWorkflowSummary.model_validate(payload)

    assert summary.counts_by_state[DisputeState.RESOLVED] == 0
    assert summary.open_disputes == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_disputes": 4},
        {"open_disputes": 1},
        {"counts_by_state": {DisputeState.PENDING: -1, DisputeState.IN_REVIEW: 3}},
        {"period_end": NOW - timedelta(days=30)},
        {"period_start": NOW.replace(tzinfo=None)},
        {"average_resolution_minutes": -1.0},
    ],
)
def test_workflow_summary_rejects_inconsistent_payloads(overrides):
    with pytest.raises(ValidationError):
        DisputeWorkflowSummary.model_validate(summary_payload(**overrides))


def test_terminal_states_and_enum_values():
    assert TERMINAL_STATES == {
        DisputeState.RESOLVED,
        DisputeState.REJECTED,
        DisputeState.WITHDRAWN,
    }
    assert DisputeTransitionAction.START_REVIEW.value == "start_review"
    assert DisputeActorRole.ADMIN.value == "admin"
