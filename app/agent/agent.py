"""Agent workflow coordinating planning, state transitions, and secure tool execution."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.agent.planner import Planner
from app.agent.state import AgentState, AgentStatus, InvalidStateTransitionError
from app.security.access import AuthorizationContext
from app.security.approvals import ApprovalStatus
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.executor import (
    ApprovalRequiredError,
    Executor,
)


class Agent:
    """Agent workflow orchestrator enforcing that all capabilities execute exclusively via Executor."""

    def __init__(
        self,
        executor: Executor | None = None,
        planner: Planner | None = None,
        repository: SQLiteRepository | None = None,
    ) -> None:
        self.executor = executor
        self.planner = planner or Planner()
        self.repository = repository or (executor.repository if executor else None)

    def prepare(self, goal: str) -> AgentState:
        """Initialize a new session and advance state through planner."""
        state = AgentState(goal=goal)
        state = self.planner.plan(state)
        if self.repository:
            self.repository.save_session(state)
        return state

    def get_session(self, session_id: UUID) -> AgentState:
        """Retrieve a persisted session by its UUID."""
        if not self.repository:
            raise RuntimeError("Repository is not configured for Agent")
        return self.repository.get_session(session_id)

    async def execute_step(
        self,
        state: AgentState,
        tool_name: str,
        raw_input: dict[str, Any],
        context: AuthorizationContext | None = None,
        reason: str = "Agent action requires approval",
    ) -> AgentState:
        """Execute a planned step exclusively through the Executor boundary."""
        if self.executor is None:
            raise RuntimeError("Executor is required to execute steps")

        state.tool_name = tool_name
        state.action = "execute"
        actor_id = context.actor_id if context else "agent"
        metadata = {
            "session_id": str(state.session_id),
            "task_id": str(state.task_id),
        }

        # Check tool permission level via Executor registry
        tool = self.executor.registry.get(tool_name)

        if tool.permission_level is PermissionLevel.CONFIRM and state.approval_id is None:
            # Action requires confirmation: create approval request through Executor
            approval = self.executor.create_approval(
                tool_name,
                raw_input,
                reason=reason,
                requested_by=actor_id,
                context=context,
                metadata=metadata,
            )
            state.approval_id = approval.id
            state.input_digest = approval.input_digest
            state.transition_to(AgentStatus.WAITING_FOR_APPROVAL)
            if self.repository:
                self.repository.save_session(state)
            return state

        # SAFE tools or previously approved actions enter EXECUTING
        state.transition_to(AgentStatus.EXECUTING)
        try:
            result = await self.executor.execute(
                tool_name,
                raw_input,
                approval_id=state.approval_id,
                context=context,
                metadata=metadata,
            )
            state.result = result
            state.transition_to(AgentStatus.COMPLETED)
            if self.repository:
                self.repository.save_session(state)
            return state
        except Exception as exc:
            state.error = str(exc)
            state.transition_to(AgentStatus.FAILED)
            if self.repository:
                self.repository.save_session(state)
            raise

    async def resume(
        self,
        state: AgentState,
        raw_input: dict[str, Any],
        context: AuthorizationContext | None = None,
    ) -> AgentState:
        """Resume an action that was previously paused in WAITING_FOR_APPROVAL."""
        if self.executor is None:
            raise RuntimeError("Executor is required to resume steps")
        if state.status is not AgentStatus.WAITING_FOR_APPROVAL:
            raise InvalidStateTransitionError(
                f"Cannot resume session with status '{state.status.value}', expected '{AgentStatus.WAITING_FOR_APPROVAL.value}'"
            )
        if state.approval_id is None or state.tool_name is None:
            state.transition_to(AgentStatus.FAILED)
            state.error = "Missing approval_id or tool_name on waiting state"
            if self.repository:
                self.repository.save_session(state)
            raise InvalidStateTransitionError("State does not have an active approval request")

        approval = self.executor.approvals.get(state.approval_id)
        metadata = {
            "session_id": str(state.session_id),
            "task_id": str(state.task_id),
        }

        if approval.status is ApprovalStatus.REJECTED:
            state.transition_to(AgentStatus.REJECTED)
            state.error = "Approval was rejected"
            if self.repository:
                self.repository.save_session(state)
            return state

        if approval.status is ApprovalStatus.EXPIRED:
            state.transition_to(AgentStatus.FAILED)
            state.error = "Approval has expired"
            if self.repository:
                self.repository.save_session(state)
            return state

        if approval.status is ApprovalStatus.APPROVED:
            state.transition_to(AgentStatus.APPROVED)
            state.transition_to(AgentStatus.EXECUTING)
            try:
                result = await self.executor.execute(
                    state.tool_name,
                    raw_input,
                    approval_id=state.approval_id,
                    context=context,
                    metadata=metadata,
                )
                state.result = result
                state.transition_to(AgentStatus.COMPLETED)
                if self.repository:
                    self.repository.save_session(state)
                return state
            except Exception as exc:
                state.error = str(exc)
                state.transition_to(AgentStatus.FAILED)
                if self.repository:
                    self.repository.save_session(state)
                raise

        # Still pending: remains in WAITING_FOR_APPROVAL
        return state
