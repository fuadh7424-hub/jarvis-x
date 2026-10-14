import asyncio
import sqlite3
import pytest
from pydantic import BaseModel

from app.agent.agent import Agent
from app.agent.state import AgentState, AgentStatus, InvalidStateTransitionError
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.base import Tool
from app.tools.executor import BlockedActionError, Executor, ToolExecutionError
from app.tools.registry import ToolRegistry
from app.tools.system_info import SystemInfoInput, SystemInfoTool


class MessageInput(BaseModel):
    message: str


class SafeEchoTool(Tool[MessageInput]):
    name = "safe_echo"
    description = "Safe echo tool"
    input_model = MessageInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: MessageInput) -> dict[str, object]:
        return {"echo": input_data.message}


class ConfirmTool(Tool[MessageInput]):
    name = "confirm_action"
    description = "Confirm action tool"
    input_model = MessageInput
    permission_level = PermissionLevel.CONFIRM

    async def execute(self, input_data: MessageInput) -> dict[str, object]:
        return {"executed": input_data.message}


class BlockedTool(Tool[MessageInput]):
    name = "blocked_action"
    description = "Blocked action tool"
    input_model = MessageInput
    permission_level = PermissionLevel.BLOCKED

    async def execute(self, input_data: MessageInput) -> dict[str, object]:
        return {"never": "runs"}


class FailingTool(Tool[MessageInput]):
    name = "failing_action"
    description = "Failing action tool"
    input_model = MessageInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: MessageInput) -> dict[str, object]:
        raise RuntimeError("boom")


def make_agent(tmp_path, *tools: Tool) -> Agent:
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    for tool in tools:
        registry.register(tool)
    repo = SQLiteRepository(tmp_path / "jarvis.sqlite3")
    executor = Executor(registry, repo, audit_sink=lambda e: None)
    return Agent(executor=executor, repository=repo)


def test_system_info_tool_is_read_only_and_safe():
    tool = SystemInfoTool()
    assert tool.permission_level is PermissionLevel.SAFE
    assert tool.name == "system_info"
    result = asyncio.run(tool.execute(SystemInfoInput(include_resources=True)))
    assert "current_time_utc" in result
    assert "os_name" in result
    assert "machine" in result
    assert "python_version" in result
    assert "cpu_count" in result


def test_sqlite_wal_mode_enabled(tmp_path):
    repo = SQLiteRepository(tmp_path / "jarvis.sqlite3")
    with sqlite3.connect(repo.database_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_agent_executes_safe_tool_through_executor(tmp_path):
    agent = make_agent(tmp_path, SafeEchoTool())
    state = agent.prepare("test echo")
    assert state.status is AgentStatus.PLANNING

    res = asyncio.run(agent.execute_step(state, "safe_echo", {"message": "hello"}))
    assert res.status is AgentStatus.COMPLETED
    assert res.result == {"echo": "hello"}
    assert res.error is None

    # Check persistence in SQLite
    saved = agent.get_session(state.session_id)
    assert saved.status is AgentStatus.COMPLETED
    assert saved.result == {"echo": "hello"}

    # Check audit events recorded session_id metadata
    audit_events = agent.executor.audit_events
    matching = [e for e in audit_events if e.metadata.get("session_id") == str(state.session_id)]
    assert len(matching) > 0
    assert any(e.event_type == "execution_succeeded" for e in matching)


def test_confirm_action_enters_waiting_for_approval_and_pauses(tmp_path):
    agent = make_agent(tmp_path, ConfirmTool())
    state = agent.prepare("run privileged task")

    res = asyncio.run(agent.execute_step(state, "confirm_action", {"message": "deploy"}))
    assert res.status is AgentStatus.WAITING_FOR_APPROVAL
    assert res.approval_id is not None
    assert res.input_digest is not None
    assert res.result is None

    # Verify session was persisted in WAITING_FOR_APPROVAL status
    saved = agent.get_session(state.session_id)
    assert saved.status is AgentStatus.WAITING_FOR_APPROVAL
    assert saved.approval_id == res.approval_id

    # Verify approval request was created in SQLite
    approval = agent.executor.approvals.get(res.approval_id)
    assert approval.tool_name == "confirm_action"
    assert approval.input_digest == res.input_digest


def test_approval_resumes_execution_to_completed(tmp_path):
    agent = make_agent(tmp_path, ConfirmTool())
    state = agent.prepare("run privileged task")

    asyncio.run(agent.execute_step(state, "confirm_action", {"message": "deploy"}))
    assert state.status is AgentStatus.WAITING_FOR_APPROVAL

    # Owner resolves approval
    agent.executor.resolve_approval(state.approval_id, approved=True)

    # Agent resumes task
    resumed = asyncio.run(agent.resume(state, {"message": "deploy"}))
    assert resumed.status is AgentStatus.COMPLETED
    assert resumed.result == {"executed": "deploy"}

    # Verify approval is now consumed
    approval = agent.executor.approvals.get(state.approval_id)
    assert approval.status.value == "consumed"


def test_rejection_stops_execution_in_rejected_state(tmp_path):
    agent = make_agent(tmp_path, ConfirmTool())
    state = agent.prepare("run privileged task")

    asyncio.run(agent.execute_step(state, "confirm_action", {"message": "destroy"}))
    assert state.status is AgentStatus.WAITING_FOR_APPROVAL

    # Owner rejects approval
    agent.executor.resolve_approval(state.approval_id, approved=False)

    # Agent resumes task
    resumed = asyncio.run(agent.resume(state, {"message": "destroy"}))
    assert resumed.status is AgentStatus.REJECTED
    assert resumed.error == "Approval was rejected"
    assert resumed.result is None

    # Check persistence
    saved = agent.get_session(state.session_id)
    assert saved.status is AgentStatus.REJECTED


def test_invalid_state_transitions_rejected():
    state = AgentState()
    assert state.status is AgentStatus.IDLE

    # IDLE cannot transition directly to COMPLETED or APPROVED
    with pytest.raises(InvalidStateTransitionError):
        state.transition_to(AgentStatus.COMPLETED)

    with pytest.raises(InvalidStateTransitionError):
        state.transition_to(AgentStatus.APPROVED)

    # WAITING_FOR_APPROVAL cannot jump directly to COMPLETED or EXECUTING
    state.status = AgentStatus.WAITING_FOR_APPROVAL
    with pytest.raises(InvalidStateTransitionError):
        state.transition_to(AgentStatus.COMPLETED)

    with pytest.raises(InvalidStateTransitionError):
        state.transition_to(AgentStatus.EXECUTING)

    # REJECTED cannot transition to EXECUTING or COMPLETED
    state.status = AgentStatus.REJECTED
    with pytest.raises(InvalidStateTransitionError):
        state.transition_to(AgentStatus.EXECUTING)

    with pytest.raises(InvalidStateTransitionError):
        state.transition_to(AgentStatus.COMPLETED)


def test_failed_execution_enters_failed_state(tmp_path):
    agent = make_agent(tmp_path, FailingTool())
    state = agent.prepare("failing task")

    with pytest.raises(ToolExecutionError):
        asyncio.run(agent.execute_step(state, "failing_action", {"message": "test"}))

    assert state.status is AgentStatus.FAILED
    assert state.error is not None
    saved = agent.get_session(state.session_id)
    assert saved.status is AgentStatus.FAILED


def test_blocked_action_enters_failed_state(tmp_path):
    agent = make_agent(tmp_path, BlockedTool())
    state = agent.prepare("blocked task")

    with pytest.raises(BlockedActionError):
        asyncio.run(agent.execute_step(state, "blocked_action", {"message": "test"}))

    assert state.status is AgentStatus.FAILED
    saved = agent.get_session(state.session_id)
    assert saved.status is AgentStatus.FAILED


def test_session_state_persists_across_restart(tmp_path):
    db_file = tmp_path / "jarvis.sqlite3"

    # Agent 1 begins task requiring approval
    agent1 = make_agent(tmp_path, ConfirmTool())
    state1 = agent1.prepare("restart test task")
    asyncio.run(agent1.execute_step(state1, "confirm_action", {"message": "persisted"}))
    assert state1.status is AgentStatus.WAITING_FOR_APPROVAL
    approval_id = state1.approval_id

    # Resolve approval
    agent1.executor.resolve_approval(approval_id, approved=True)

    # Simulate service restart: create fresh Agent/Executor from the same DB file
    registry2 = ToolRegistry()
    registry2.register(ConfirmTool())
    repo2 = SQLiteRepository(db_file)
    executor2 = Executor(registry2, repo2, audit_sink=lambda e: None)
    agent2 = Agent(executor=executor2, repository=repo2)

    # Retrieve persisted session
    loaded_state = agent2.get_session(state1.session_id)
    assert loaded_state.status is AgentStatus.WAITING_FOR_APPROVAL
    assert loaded_state.approval_id == approval_id
    assert loaded_state.tool_name == "confirm_action"

    # Resume the interrupted task
    resumed = asyncio.run(agent2.resume(loaded_state, {"message": "persisted"}))
    assert resumed.status is AgentStatus.COMPLETED
    assert resumed.result == {"executed": "persisted"}


def test_agent_without_executor_cannot_execute():
    agent = Agent()
    state = AgentState()
    with pytest.raises(RuntimeError, match="Executor is required"):
        asyncio.run(agent.execute_step(state, "system_info", {}))
    with pytest.raises(RuntimeError, match="Executor is required"):
        asyncio.run(agent.resume(state, {}))
