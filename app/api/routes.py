"""Minimal HTTP interface for health, approvals, and controlled execution."""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.security.approvals import ApprovalRequest
from app.tools.executor import (
    ApprovalRejectedError,
    ApprovalRequiredError,
    BlockedActionError,
    ExecutionError,
    InvalidToolInputError,
    ToolExecutionError,
    UnknownToolError,
)

router = APIRouter()


class ApprovalCreate(BaseModel):
    tool_name: str
    input: dict[str, Any]
    reason: str = Field(min_length=1, max_length=500)
    requested_by: str = Field(default="api", min_length=1, max_length=100)


class ApprovalResolution(BaseModel):
    approved: bool


class ExecutionRequest(BaseModel):
    input: dict[str, Any]
    approval_id: UUID | None = None


def _executor(request: Request):
    return request.app.state.executor


def _http_error(exc: ExecutionError) -> HTTPException:
    if isinstance(exc, UnknownToolError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InvalidToolInputError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (ApprovalRequiredError, ApprovalRejectedError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, BlockedActionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ToolExecutionError):
        return HTTPException(status_code=500, detail="Tool execution failed")
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "jarvis-x"}


@router.get("/approvals", response_model=list[ApprovalRequest], tags=["approvals"])
async def list_approvals(request: Request) -> tuple[ApprovalRequest, ...]:
    return _executor(request).approvals.list()


@router.post("/approvals", response_model=ApprovalRequest, status_code=201, tags=["approvals"])
async def create_approval(payload: ApprovalCreate, request: Request) -> ApprovalRequest:
    try:
        return _executor(request).create_approval(payload.tool_name, payload.input, payload.reason, payload.requested_by)
    except ExecutionError as exc:
        raise _http_error(exc) from exc


@router.post("/approvals/{approval_id}/resolve", response_model=ApprovalRequest, tags=["approvals"])
async def resolve_approval(approval_id: UUID, payload: ApprovalResolution, request: Request) -> ApprovalRequest:
    try:
        return _executor(request).resolve_approval(approval_id, payload.approved)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Approval request was not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/actions/{tool_name}/execute", tags=["actions"])
async def execute_action(tool_name: str, payload: ExecutionRequest, request: Request) -> dict[str, object]:
    try:
        return await _executor(request).execute(tool_name, payload.input, payload.approval_id)
    except ExecutionError as exc:
        raise _http_error(exc) from exc
