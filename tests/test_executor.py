import asyncio

import pytest
from pydantic import BaseModel

from app.security.approvals import ApprovalManager, ApprovalStatus
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import (
    ApprovalRejectedError,
    ApprovalRequiredError,
    BlockedActionError,
    InvalidToolInputError,
    ToolExecutionError,
    UnknownToolError,
    Executor,
)
from app.tools.registry import ToolRegistry


class NumberInput(BaseModel):
    value: int


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


class BlockedTool(SafeTool):
    name = "blocked"
    permission_level = PermissionLevel.BLOCKED


class FailingTool(SafeTool):
    name = "failing"

    async def execute(self, input_data: NumberInput) -> dict[str, object]:
        raise RuntimeError("expected test failure")


def make_executor(*tools: Tool) -> Executor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return Executor(registry, ApprovalManager(), audit_sink=lambda event: None)


def test_safe_execution_and_audit_event():
    executor = make_executor(SafeTool())

    assert asyncio.run(executor.execute("safe", {"value": 3})) == {"value": 3}
    event = executor.audit_events[-1]
    assert event.tool == "safe"
    assert event.result == "success"


def test_confirm_action_requires_explicit_approval():
    executor = make_executor(ConfirmTool())

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("confirm", {"value": 3}))
    assert executor.audit_events[-1].error == "approval_required"


def test_confirm_action_executes_after_matching_approval():
    executor = make_executor(ConfirmTool())
    approval = executor.create_approval("confirm", {"value": 3}, "test confirmation", "test")
    executor.resolve_approval(approval.id, approved=True)

    assert asyncio.run(executor.execute("confirm", {"value": 3}, approval.id)) == {"value": 3}
    assert executor.audit_events[-1].approval_status is None
    assert executor.audit_events[-1].result == "success"


def test_rejected_approval_cannot_execute():
    executor = make_executor(ConfirmTool())
    approval = executor.create_approval("confirm", {"value": 3}, "test confirmation", "test")
    executor.resolve_approval(approval.id, approved=False)

    with pytest.raises(ApprovalRejectedError):
        asyncio.run(executor.execute("confirm", {"value": 3}, approval.id))
    assert executor.audit_events[-1].approval_status is ApprovalStatus.REJECTED


def test_blocked_action_never_executes():
    executor = make_executor(BlockedTool())

    with pytest.raises(BlockedActionError):
        asyncio.run(executor.execute("blocked", {"value": 3}))
    assert executor.audit_events[-1].error == "blocked"


def test_unknown_tool_is_rejected_and_audited():
    executor = make_executor()

    with pytest.raises(UnknownToolError):
        asyncio.run(executor.execute("missing", {}))
    assert executor.audit_events[-1].error == "unknown_tool"


def test_invalid_tool_input_is_rejected_and_audited():
    executor = make_executor(SafeTool())

    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("safe", {"value": "not-a-number"}))
    assert executor.audit_events[-1].error == "invalid_input"


def test_executor_failure_is_audited_and_does_not_leak_tool_exception():
    executor = make_executor(FailingTool())

    with pytest.raises(ToolExecutionError):
        asyncio.run(executor.execute("failing", {"value": 3}))
    assert executor.audit_events[-1].error == "execution_failed"
