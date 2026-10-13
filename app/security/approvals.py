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
    EXPIRED = "expired"
    CONSUMED = "consumed"


class ApprovalRequest(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    action: str
    reason: str
    requested_by: str
    risk_level: PermissionLevel
    tool_name: str | None = None
    input_digest: str | None = None
    input_data: dict[str, Any] | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_at: datetime | None = None
    expires_at: datetime | None = None
    consumed_at: datetime | None = None
    resolution: str | None = None


class AuditEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = "audit"
    tool: str
    action: str
    permission_level: PermissionLevel
    approval_id: UUID | None = None
    approval_status: ApprovalStatus | None = None
    result: str | None = None
    error: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
