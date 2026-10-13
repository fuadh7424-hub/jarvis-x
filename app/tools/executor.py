"""Policy-enforcing execution boundary for registered tools."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.core.logging import log_audit_event
from app.security.access import AuthorizationContext, AuthorizationPolicy
from app.security.approvals import ApprovalRequest, ApprovalStatus, AuditEvent
from app.security.permissions import PermissionLevel, PermissionPolicy
from app.storage.sqlite import ApprovalStateError, SQLiteRepository
from app.tools.registry import ToolRegistry


class ExecutionError(Exception):
    """Base error for controlled execution failures."""


class UnknownToolError(ExecutionError): pass
class InvalidToolInputError(ExecutionError): pass
class ApprovalRequiredError(ExecutionError): pass
class ApprovalRejectedError(ExecutionError): pass
class BlockedActionError(ExecutionError): pass
class ApprovalMismatchError(ExecutionError): pass
class ApprovalConsumedError(ExecutionError): pass
class ApprovalExpiredError(ExecutionError): pass
class ToolExecutionError(ExecutionError): pass


class Executor:
    """Validate, authorize, approve, and execute only registered tools."""

    def __init__(self, registry: ToolRegistry, repository: SQLiteRepository, *, approval_ttl_seconds: int = 300, audit_sink: Callable[[AuditEvent], None] = log_audit_event, now: Callable[[], datetime] | None = None) -> None:
        self.registry = registry
        self.repository = repository
        self.approval_ttl = timedelta(seconds=approval_ttl_seconds)
        self.audit_sink = audit_sink
        self.now = now or (lambda: datetime.now(timezone.utc))

    @property
    def approvals(self) -> SQLiteRepository:
        return self.repository

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return self.repository.list_audit_events()

    @staticmethod
    def _input_digest(input_data: Any) -> str:
        canonical = json.dumps(input_data.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _audit(self, tool: str, action: str, level: PermissionLevel, *, event_type: str, approval_id: UUID | None = None, approval_status: ApprovalStatus | None = None, result: str | None = None, error: str | None = None) -> None:
        # Inputs, reasons, actor identity, and exception text are deliberately excluded.
        event = AuditEvent(tool=tool, action=action, permission_level=level, event_type=event_type, approval_id=approval_id, approval_status=approval_status, result=result, error=error)
        self.repository.append_audit_event(event)
        self.audit_sink(event)

    def _tool(self, tool_name: str):
        try:
            tool = self.registry.get(tool_name)
        except KeyError as exc:
            self._audit(tool_name, "execute", PermissionLevel.BLOCKED, event_type="unknown_tool", error="unknown_tool")
            raise UnknownToolError(f"Unknown tool: {tool_name}") from exc
        self._audit(tool.name, "execute", tool.permission_level, event_type="tool_requested", result="received")
        return tool

    def _validated_input(self, tool: Any, raw_input: dict[str, Any]):
        try:
            input_data = tool.input_model.model_validate(raw_input)
        except ValidationError as exc:
            self._audit(tool.name, "execute", tool.permission_level, event_type="validation_failed", error="invalid_input")
            raise InvalidToolInputError("Tool input does not match its declared schema") from exc
        self._audit(tool.name, "execute", tool.permission_level, event_type="validation_succeeded", result="valid")
        return input_data

    def create_approval(self, tool_name: str, raw_input: dict[str, Any], reason: str, requested_by: str, context: AuthorizationContext | None = None) -> ApprovalRequest:
        AuthorizationPolicy.require_request(context or AuthorizationContext(requested_by))
        tool = self._tool(tool_name)
        input_data = self._validated_input(tool, raw_input)
        if tool.permission_level is PermissionLevel.BLOCKED:
            self._audit(tool.name, "approval_request", tool.permission_level, event_type="blocked_action", error="blocked")
            raise BlockedActionError("Blocked actions cannot be approved")
        if not PermissionPolicy.requires_approval(tool.permission_level):
            raise ExecutionError("Approval is only available for CONFIRM-level actions")
        timestamp = self.now()
        request = ApprovalRequest(action="execute", reason="Approval requested", requested_by=(context.actor_id if context else requested_by), risk_level=tool.permission_level, tool_name=tool.name, input_digest=self._input_digest(input_data), timestamp=timestamp, expires_at=timestamp + self.approval_ttl)
        self.repository.create_approval(request)
        self._audit(tool.name, "approval_request", tool.permission_level, event_type="approval_created", approval_id=request.id, approval_status=request.status, result="pending")
        return request

    def resolve_approval(self, request_id: UUID, approved: bool, context: AuthorizationContext | None = None) -> ApprovalRequest:
        AuthorizationPolicy.require_resolution(context or AuthorizationContext("local"))
        try:
            request = self.repository.resolve(request_id, approved, self.now())
        except ApprovalStateError as exc:
            self._audit("approval", "approval_resolution", PermissionLevel.CONFIRM, event_type="approval_resolution_failed", approval_id=request_id, error="invalid_approval_state")
            raise ExecutionError(str(exc)) from exc
        if request.status is ApprovalStatus.EXPIRED:
            self._audit(request.tool_name or "approval", "approval_resolution", request.risk_level, event_type="approval_resolution_failed", approval_id=request.id, approval_status=request.status, error="expired")
            raise ExecutionError("Approval request has expired")
        self._audit(request.tool_name or "approval", "approval_resolution", request.risk_level, event_type="approval_resolved", approval_id=request.id, approval_status=request.status, result=request.status.value)
        return request

    async def execute(self, tool_name: str, raw_input: dict[str, Any], approval_id: UUID | None = None, context: AuthorizationContext | None = None) -> dict[str, object]:
        AuthorizationPolicy.require_execution(context or AuthorizationContext("local"))
        tool = self._tool(tool_name)
        input_data = self._validated_input(tool, raw_input)
        self._audit(tool.name, "execute", tool.permission_level, event_type="permission_decision", result="allowed" if tool.permission_level is PermissionLevel.SAFE else "approval_required")
        if tool.permission_level is PermissionLevel.BLOCKED:
            self._audit(tool.name, "execute", tool.permission_level, event_type="blocked_action", error="blocked")
            raise BlockedActionError("This action is blocked by policy")
        if tool.permission_level is PermissionLevel.CONFIRM:
            if approval_id is None:
                self._audit(tool.name, "execute", tool.permission_level, event_type="approval_required", error="approval_required")
                raise ApprovalRequiredError("Explicit approval is required")
            try:
                approval = self.repository.consume(approval_id, tool.name, self._input_digest(input_data), self.now())
            except KeyError as exc:
                self._audit(tool.name, "execute", tool.permission_level, event_type="approval_required", approval_id=approval_id, error="unknown_approval")
                raise ApprovalRequiredError("Approval request was not found") from exc
            except ApprovalStateError as exc:
                message = str(exc)
                error = "approval_mismatch" if "match" in message else "approval_invalid"
                self._audit(tool.name, "execute", tool.permission_level, event_type="approval_consumption_failed", approval_id=approval_id, error=error)
                if "rejected" in message:
                    raise ApprovalRejectedError("Approval was rejected") from exc
                if "consumed" in message:
                    raise ApprovalConsumedError("Approval was already consumed") from exc
                if "expired" in message:
                    raise ApprovalExpiredError("Approval has expired") from exc
                if "match" in message:
                    raise ApprovalMismatchError("Approval does not match this action") from exc
                raise ApprovalRequiredError("Approval is not available") from exc
            if approval.status is ApprovalStatus.EXPIRED:
                self._audit(tool.name, "execute", tool.permission_level, event_type="approval_consumption_failed", approval_id=approval.id, approval_status=approval.status, error="expired")
                raise ApprovalExpiredError("Approval has expired")
            self._audit(tool.name, "execute", tool.permission_level, event_type="approval_consumed", approval_id=approval.id, approval_status=approval.status, result="consumed")
        self._audit(tool.name, "execute", tool.permission_level, event_type="execution_started", approval_id=approval_id, result="started")
        try:
            result = await tool.execute(input_data)
        except Exception as exc:
            self._audit(tool.name, "execute", tool.permission_level, event_type="execution_failed", approval_id=approval_id, error="execution_failed")
            raise ToolExecutionError("Tool execution failed") from exc
        self._audit(tool.name, "execute", tool.permission_level, event_type="execution_succeeded", approval_id=approval_id, result="success")
        return result
