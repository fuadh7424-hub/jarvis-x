"""Policy-enforcing execution boundary for registered tools."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.core.logging import log_audit_event
from app.security.approvals import ApprovalManager, ApprovalRequest, ApprovalStatus, AuditEvent
from app.security.permissions import PermissionLevel, PermissionPolicy
from app.tools.registry import ToolRegistry


class ExecutionError(Exception):
    """Base error for controlled execution failures."""


class UnknownToolError(ExecutionError):
    pass


class InvalidToolInputError(ExecutionError):
    pass


class ApprovalRequiredError(ExecutionError):
    pass


class ApprovalRejectedError(ExecutionError):
    pass


class BlockedActionError(ExecutionError):
    pass


class ApprovalMismatchError(ExecutionError):
    pass


class ToolExecutionError(ExecutionError):
    pass


class Executor:
    """Execute only registered, validated tools after independent policy checks."""

    def __init__(self, registry: ToolRegistry, approvals: ApprovalManager, audit_sink: Callable[[AuditEvent], None] = log_audit_event) -> None:
        self.registry = registry
        self.approvals = approvals
        self.audit_sink = audit_sink
        self._events: list[AuditEvent] = []

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def _audit(self, tool: str, action: str, level: PermissionLevel, *, approval_status: ApprovalStatus | None = None, result: str | None = None, error: str | None = None) -> None:
        event = AuditEvent(tool=tool, action=action, permission_level=level, approval_status=approval_status, result=result, error=error)
        self._events.append(event)
        self.audit_sink(event)

    def _tool(self, tool_name: str):
        try:
            return self.registry.get(tool_name)
        except KeyError as exc:
            self._audit(tool_name, "execute", PermissionLevel.BLOCKED, error="unknown_tool")
            raise UnknownToolError(f"Unknown tool: {tool_name}") from exc

    def _validated_input(self, tool: Any, raw_input: dict[str, Any]):
        try:
            return tool.input_model.model_validate(raw_input)
        except ValidationError as exc:
            self._audit(tool.name, "execute", tool.permission_level, error="invalid_input")
            raise InvalidToolInputError("Tool input does not match its declared schema") from exc

    def create_approval(self, tool_name: str, raw_input: dict[str, Any], reason: str, requested_by: str) -> ApprovalRequest:
        tool = self._tool(tool_name)
        input_data = self._validated_input(tool, raw_input)
        if tool.permission_level is PermissionLevel.BLOCKED:
            self._audit(tool.name, "approval_request", tool.permission_level, error="blocked")
            raise BlockedActionError("Blocked actions cannot be approved")
        if not PermissionPolicy.requires_approval(tool.permission_level):
            raise ExecutionError("Approval is only available for CONFIRM-level actions")
        request = ApprovalRequest(action="execute", reason=reason, requested_by=requested_by, risk_level=tool.permission_level, tool_name=tool.name, input_data=input_data.model_dump(mode="json"))
        self.approvals.request(request)
        self._audit(tool.name, "approval_request", tool.permission_level, approval_status=request.status, result="pending")
        return request

    def resolve_approval(self, request_id: UUID, approved: bool) -> ApprovalRequest:
        request = self.approvals.resolve(request_id, approved)
        self._audit(request.tool_name or "approval", "approval_resolution", request.risk_level, approval_status=request.status, result=request.status.value)
        return request

    async def execute(self, tool_name: str, raw_input: dict[str, Any], approval_id: UUID | None = None) -> dict[str, object]:
        tool = self._tool(tool_name)
        input_data = self._validated_input(tool, raw_input)
        if tool.permission_level is PermissionLevel.BLOCKED:
            self._audit(tool.name, "execute", tool.permission_level, error="blocked")
            raise BlockedActionError("This action is blocked by policy")
        if tool.permission_level is PermissionLevel.CONFIRM:
            if approval_id is None:
                self._audit(tool.name, "execute", tool.permission_level, error="approval_required")
                raise ApprovalRequiredError("Explicit approval is required")
            try:
                approval = self.approvals.get(approval_id)
            except KeyError as exc:
                self._audit(tool.name, "execute", tool.permission_level, error="unknown_approval")
                raise ApprovalRequiredError("Approval request was not found") from exc
            if approval.status is ApprovalStatus.REJECTED:
                self._audit(tool.name, "execute", tool.permission_level, approval_status=approval.status, error="approval_rejected")
                raise ApprovalRejectedError("Approval was rejected")
            if approval.status is not ApprovalStatus.APPROVED:
                self._audit(tool.name, "execute", tool.permission_level, approval_status=approval.status, error="approval_required")
                raise ApprovalRequiredError("Approval is still pending")
            if approval.tool_name != tool.name or approval.input_data != input_data.model_dump(mode="json"):
                self._audit(tool.name, "execute", tool.permission_level, approval_status=approval.status, error="approval_mismatch")
                raise ApprovalMismatchError("Approval does not match this action")
        try:
            result = await tool.execute(input_data)
        except Exception as exc:
            self._audit(tool.name, "execute", tool.permission_level, error="execution_failed")
            raise ToolExecutionError("Tool execution failed") from exc
        self._audit(tool.name, "execute", tool.permission_level, result="success")
        return result
