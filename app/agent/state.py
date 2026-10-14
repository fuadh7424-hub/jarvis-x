from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    STOPPED = "stopped"


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal agent state transition is attempted."""
    pass


VALID_TRANSITIONS: dict[AgentStatus, set[AgentStatus]] = {
    AgentStatus.IDLE: {
        AgentStatus.PLANNING,
        AgentStatus.EXECUTING,
        AgentStatus.WAITING_FOR_APPROVAL,
        AgentStatus.STOPPED,
    },
    AgentStatus.PLANNING: {
        AgentStatus.EXECUTING,
        AgentStatus.WAITING_FOR_APPROVAL,
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.STOPPED,
    },
    AgentStatus.WAITING_FOR_APPROVAL: {
        AgentStatus.APPROVED,
        AgentStatus.REJECTED,
        AgentStatus.FAILED,
        AgentStatus.STOPPED,
    },
    AgentStatus.APPROVED: {
        AgentStatus.EXECUTING,
        AgentStatus.FAILED,
        AgentStatus.STOPPED,
    },
    AgentStatus.EXECUTING: {
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.WAITING_FOR_APPROVAL,
        AgentStatus.STOPPED,
    },
    AgentStatus.REJECTED: {
        AgentStatus.STOPPED,
        AgentStatus.FAILED,
    },
    AgentStatus.COMPLETED: {
        AgentStatus.IDLE,
    },
    AgentStatus.FAILED: set(),
    AgentStatus.STOPPED: set(),
}


class AgentState(BaseModel):
    session_id: UUID = Field(default_factory=uuid4)
    task_id: UUID = Field(default_factory=uuid4)
    status: AgentStatus = AgentStatus.IDLE
    goal: str | None = None
    action: str | None = None
    tool_name: str | None = None
    input_digest: str | None = None
    approval_id: UUID | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, str] = Field(default_factory=dict)

    def transition_to(self, new_status: AgentStatus, *, now: datetime | None = None) -> None:
        if new_status not in VALID_TRANSITIONS.get(self.status, set()):
            raise InvalidStateTransitionError(
                f"Cannot transition AgentState from '{self.status.value}' to '{new_status.value}'"
            )
        self.status = new_status
        self.updated_at = now or datetime.now(timezone.utc)

