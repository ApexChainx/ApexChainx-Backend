"""Workspace-local contracts for SLA dispute workflow states and transitions (#851).

These models intentionally define their own state and action enums so workspace
services can describe a dispute lifecycle without importing core ORM classes.
"""

from datetime import datetime
from enum import Enum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DisputeState(str, Enum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class DisputeTransitionAction(str, Enum):
    START_REVIEW = "start_review"
    RETURN_TO_PENDING = "return_to_pending"
    RESOLVE = "resolve"
    REJECT = "reject"
    WITHDRAW = "withdraw"
    REOPEN = "reopen"


class DisputeActorRole(str, Enum):
    ENGINEER = "engineer"
    ADMIN = "admin"
    SYSTEM = "system"


TERMINAL_STATES = frozenset({DisputeState.RESOLVED, DisputeState.REJECTED, DisputeState.WITHDRAWN})

ACTION_SOURCES = {
    DisputeTransitionAction.START_REVIEW: frozenset({DisputeState.PENDING}),
    DisputeTransitionAction.RETURN_TO_PENDING: frozenset({DisputeState.IN_REVIEW}),
    DisputeTransitionAction.RESOLVE: frozenset({DisputeState.IN_REVIEW}),
    DisputeTransitionAction.REJECT: frozenset({DisputeState.IN_REVIEW}),
    DisputeTransitionAction.WITHDRAW: frozenset({DisputeState.PENDING, DisputeState.IN_REVIEW}),
    DisputeTransitionAction.REOPEN: frozenset({DisputeState.RESOLVED, DisputeState.REJECTED}),
}

ACTION_TARGETS = {
    DisputeTransitionAction.START_REVIEW: DisputeState.IN_REVIEW,
    DisputeTransitionAction.RETURN_TO_PENDING: DisputeState.PENDING,
    DisputeTransitionAction.RESOLVE: DisputeState.RESOLVED,
    DisputeTransitionAction.REJECT: DisputeState.REJECTED,
    DisputeTransitionAction.WITHDRAW: DisputeState.WITHDRAWN,
    DisputeTransitionAction.REOPEN: DisputeState.IN_REVIEW,
}

REASON_REQUIRED_ACTIONS = frozenset({DisputeTransitionAction.RESOLVE, DisputeTransitionAction.REJECT})


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class DisputeTransitionRequest(BaseModel):
    """Requested transition for a dispute currently in `expected_state`."""

    model_config = ConfigDict(extra="forbid")

    action: DisputeTransitionAction
    expected_state: DisputeState
    actor: str = Field(..., min_length=1, max_length=255, description="User id or service identity")
    actor_role: DisputeActorRole = DisputeActorRole.ENGINEER
    reason: str | None = Field(default=None, min_length=10, max_length=1000)
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def requested_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def reason_required_for_final_actions(self) -> Self:
        if self.action in REASON_REQUIRED_ACTIONS and self.reason is None:
            raise ValueError("resolve and reject transitions require a reason")
        return self


class DisputeTransition(BaseModel):
    """Recorded state change, validated against the workspace transition map."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    action: DisputeTransitionAction
    from_state: DisputeState
    to_state: DisputeState
    actor: str = Field(..., min_length=1, max_length=255)
    actor_role: DisputeActorRole
    reason: str | None = Field(default=None, min_length=10, max_length=1000)
    notes: str | None = Field(default=None, max_length=1000)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def transition_is_allowed(self) -> Self:
        if self.from_state not in ACTION_SOURCES[self.action]:
            raise ValueError(f"{self.action.value} is not allowed from {self.from_state.value}")
        if self.to_state is not ACTION_TARGETS[self.action]:
            raise ValueError(f"{self.action.value} must transition to {ACTION_TARGETS[self.action].value}")
        if self.action in REASON_REQUIRED_ACTIONS and self.reason is None:
            raise ValueError("resolve and reject transitions require a reason")
        return self


class DisputeWorkflowState(BaseModel):
    """Current workflow state of one SLA dispute."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    dispute_id: UUID
    sla_result_id: int = Field(..., gt=0, description="SLA calculation result under dispute")
    state: DisputeState
    flagged_by: str = Field(..., min_length=1, max_length=255)
    flagged_at: datetime
    updated_at: datetime
    transition_count: int = Field(..., ge=0)
    latest_transition: DisputeTransition | None = None

    @field_validator("flagged_at", "updated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def state_matches_history(self) -> Self:
        if self.updated_at < self.flagged_at:
            raise ValueError("updated_at cannot precede flagged_at")
        if self.latest_transition is not None:
            if self.latest_transition.to_state is not self.state:
                raise ValueError("latest_transition must end in the current state")
            if self.latest_transition.occurred_at < self.flagged_at:
                raise ValueError("latest_transition cannot precede flagged_at")
        return self


class DisputeWorkflowSummary(BaseModel):
    """Aggregated dispute counts for a closed reporting period."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    period_start: datetime
    period_end: datetime
    total_disputes: int = Field(..., ge=0)
    counts_by_state: dict[DisputeState, int] = Field(default_factory=dict)
    open_disputes: int = Field(..., ge=0, description="Pending plus in-review disputes")
    average_resolution_minutes: float | None = Field(
        default=None, ge=0.0, description="Mean flag-to-terminal time; null when nothing has closed"
    )

    @field_validator("period_start", "period_end")
    @classmethod
    def period_boundaries_are_aware(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> Self:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        if any(count < 0 for count in self.counts_by_state.values()):
            raise ValueError("state counts must be non-negative")
        for state in DisputeState:
            self.counts_by_state.setdefault(state, 0)
        if sum(self.counts_by_state.values()) != self.total_disputes:
            raise ValueError("counts_by_state must sum to total_disputes")
        expected_open = self.counts_by_state[DisputeState.PENDING] + self.counts_by_state[DisputeState.IN_REVIEW]
        if self.open_disputes != expected_open:
            raise ValueError("open_disputes must equal pending plus in-review disputes")
        return self


__all__ = [
    "ACTION_SOURCES",
    "ACTION_TARGETS",
    "TERMINAL_STATES",
    "DisputeActorRole",
    "DisputeState",
    "DisputeTransition",
    "DisputeTransitionAction",
    "DisputeTransitionRequest",
    "DisputeWorkflowState",
    "DisputeWorkflowSummary",
]
