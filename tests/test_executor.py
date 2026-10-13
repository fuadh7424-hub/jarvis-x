import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from app.security.approvals import ApprovalStatus
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.base import Tool
from app.tools.executor import ApprovalConsumedError, ApprovalExpiredError, ApprovalMismatchError, ApprovalRejectedError, ApprovalRequiredError, BlockedActionError, Executor, InvalidToolInputError, ToolExecutionError, UnknownToolError
from app.tools.registry import ToolRegistry


class NumberInput(BaseModel):
    value: int


class TextInput(BaseModel):
    text: str


class SafeTool(Tool[NumberInput]):
    name = "safe"
    description = "Test safe tool"
    input_model = NumberInput
    permission_level = PermissionLevel.SAFE

    async def execute(self, input_data: NumberInput) -> dict[str, object]:
        return {"value": input_data.value}


class ConfirmTool(SafeTool):
    name = "confirm"
    permission_level = PermissionLevel.CONFIRM


class OtherConfirmTool(ConfirmTool):
    name = "other-confirm"


class TextConfirmTool(Tool[TextInput]):
    name = "text-confirm"
    description = "Test confirm tool accepting text"
    input_model = TextInput
    permission_level = PermissionLevel.CONFIRM

    async def execute(self, input_data: TextInput) -> dict[str, object]:
        return {"length": len(input_data.text)}


class BlockedTool(SafeTool):
    name = "blocked"
    permission_level = PermissionLevel.BLOCKED


class FailingTool(SafeTool):
    name = "failing"

    async def execute(self, input_data: NumberInput) -> dict[str, object]:
        raise RuntimeError("expected test failure")


def make_executor(tmp_path, *tools: Tool, now=None, ttl=300) -> Executor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return Executor(registry, SQLiteRepository(tmp_path / "jarvis.sqlite3"), approval_ttl_seconds=ttl, audit_sink=lambda event: None, now=now)


def approve(executor: Executor, request_id):
    return executor.resolve_approval(request_id, approved=True)


def test_database_initialization_and_safe_execution(tmp_path):
    executor = make_executor(tmp_path, SafeTool())
    assert executor.repository.database_path.exists()
    assert asyncio.run(executor.execute("safe", {"value": 3})) == {"value": 3}
    assert any(event.event_type == "execution_succeeded" for event in executor.audit_events)


def test_confirm_action_requires_explicit_approval(tmp_path):
    executor = make_executor(tmp_path, ConfirmTool())
    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("confirm", {"value": 3}))


def test_approval_persists_across_repository_restart_and_executes_once(tmp_path):
    executor = make_executor(tmp_path, ConfirmTool())
    request = executor.create_approval("confirm", {"value": 3}, "test", "test")
    approve(executor, request.id)
    restarted = make_executor(tmp_path, ConfirmTool())
    assert restarted.approvals.get(request.id).status is ApprovalStatus.APPROVED
    assert asyncio.run(restarted.execute("confirm", {"value": 3}, request.id)) == {"value": 3}
    assert restarted.approvals.get(request.id).status is ApprovalStatus.CONSUMED
    with pytest.raises(ApprovalConsumedError):
        asyncio.run(restarted.execute("confirm", {"value": 3}, request.id))


def test_rejected_approval_cannot_execute(tmp_path):
    executor = make_executor(tmp_path, ConfirmTool())
    request = executor.create_approval("confirm", {"value": 3}, "test", "test")
    executor.resolve_approval(request.id, approved=False)
    with pytest.raises(ApprovalRejectedError):
        asyncio.run(executor.execute("confirm", {"value": 3}, request.id))


def test_invalid_state_transition_is_rejected(tmp_path):
    executor = make_executor(tmp_path, ConfirmTool())
    request = executor.create_approval("confirm", {"value": 3}, "test", "test")
    approve(executor, request.id)
    with pytest.raises(Exception, match="not pending"):
        executor.resolve_approval(request.id, approved=True)


def test_expired_approval_is_persisted_and_cannot_execute(tmp_path):
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    executor = make_executor(tmp_path, ConfirmTool(), now=lambda: clock[0], ttl=10)
    request = executor.create_approval("confirm", {"value": 3}, "test", "test")
    approve(executor, request.id)
    clock[0] += timedelta(seconds=11)
    with pytest.raises(ApprovalExpiredError):
        asyncio.run(executor.execute("confirm", {"value": 3}, request.id))
    assert executor.approvals.get(request.id).status is ApprovalStatus.EXPIRED


def test_approved_input_and_tool_mismatch_are_rejected(tmp_path):
    executor = make_executor(tmp_path, ConfirmTool(), OtherConfirmTool())
    request = executor.create_approval("confirm", {"value": 3}, "test", "test")
    approve(executor, request.id)
    with pytest.raises(ApprovalMismatchError):
        asyncio.run(executor.execute("confirm", {"value": 4}, request.id))
    with pytest.raises(ApprovalMismatchError):
        asyncio.run(executor.execute("other-confirm", {"value": 3}, request.id))
    assert executor.approvals.get(request.id).status is ApprovalStatus.APPROVED


def test_unknown_blocked_and_invalid_input_are_rejected(tmp_path):
    executor = make_executor(tmp_path, SafeTool(), BlockedTool())
    with pytest.raises(UnknownToolError):
        asyncio.run(executor.execute("missing", {}))
    with pytest.raises(BlockedActionError):
        asyncio.run(executor.execute("blocked", {"value": 3}))
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("safe", {"value": "not-a-number"}))


def test_failed_execution_and_audit_events_are_durable_and_safe(tmp_path):
    executor = make_executor(tmp_path, FailingTool(), SafeTool(), TextConfirmTool())
    with pytest.raises(ToolExecutionError):
        asyncio.run(executor.execute("failing", {"value": 3}))
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("safe", {"value": "SUPER_SECRET_TOKEN"}))
    request = executor.create_approval("text-confirm", {"text": "SUPER_SECRET_TOKEN"}, "contains secret", "test")
    assert request.input_data is None
    events = SQLiteRepository(tmp_path / "jarvis.sqlite3").list_audit_events()
    assert any(event.event_type == "execution_failed" for event in events)
    assert all("SUPER_SECRET_TOKEN" not in event.model_dump_json() for event in events)
    assert b"SUPER_SECRET_TOKEN" not in (tmp_path / "jarvis.sqlite3").read_bytes()


def test_duplicate_execution_cannot_consume_approval_twice(tmp_path):
    executor = make_executor(tmp_path, ConfirmTool())
    request = executor.create_approval("confirm", {"value": 3}, "test", "test")
    approve(executor, request.id)

    def execute_once():
        try:
            return asyncio.run(executor.execute("confirm", {"value": 3}, request.id))
        except ApprovalConsumedError:
            return "consumed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute_once(), range(2)))
    assert results.count({"value": 3}) == 1
    assert results.count("consumed") == 1
