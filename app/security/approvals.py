"""In-memory approval workflow and audit event contract."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.security.permissions import PermissionLevel


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    action: str
    reason: str
    requested_by: str
    risk_level: PermissionLevel
    tool_name: str | None = None
    input_data: dict[str, Any] | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ApprovalStatus = ApprovalStatus.PENDING


class AuditEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool: str
    action: str
    permission_level: PermissionLevel
    approval_status: ApprovalStatus | None = None
    result: str | None = None
    error: str | None = None


class ApprovalManager:
    """Transient approval store. Persistence is intentionally deferred."""
    def __init__(self) -> None:
        self._requests: dict[UUID, ApprovalRequest] = {}

    def request(self, request: ApprovalRequest) -> ApprovalRequest:
        self._requests[request.id] = request
        return request

    def resolve(self, request_id: UUID, approved: bool) -> ApprovalRequest:
        request = self._requests[request_id]
        if request.status is not ApprovalStatus.PENDING:
            raise ValueError("Approval request is already resolved")
        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        return request

    def get(self, request_id: UUID) -> ApprovalRequest:
        return self._requests[request_id]

    def list(self) -> tuple[ApprovalRequest, ...]:
        return tuple(self._requests.values())
