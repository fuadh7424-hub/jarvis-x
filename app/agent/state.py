"""Typed state passed through future agent workflows."""
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class AgentState(BaseModel):
    session_id: UUID = Field(default_factory=uuid4)
    status: AgentStatus = AgentStatus.IDLE
    goal: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

