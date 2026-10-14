"""Planning boundary for agent workflows."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from app.agent.state import AgentState, AgentStatus


class PlanStep(BaseModel):
    action: str = "execute"
    tool_name: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    reason: str = "Agent planned step"


class Planner:
    """Deliberate planning boundary; does not execute actions directly."""

    def plan(self, state: AgentState) -> AgentState:
        """Advance agent state into PLANNING if IDLE."""
        if state.status is AgentStatus.IDLE:
            state.transition_to(AgentStatus.PLANNING)
        return state

    def create_step(self, tool_name: str, input_data: dict[str, Any], reason: str = "Agent step") -> PlanStep:
        return PlanStep(tool_name=tool_name, input_data=input_data, reason=reason)

