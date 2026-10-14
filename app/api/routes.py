"""Protected HTTP interface for JARVIS-X."""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.security.access import AuthorizationAction, AuthorizationContext, AuthorizationError, AuthorizationPolicy
from app.security.approvals import ApprovalRequest, AuditEvent
from app.security.authentication import AuthenticationError
from app.tools.executor import ApprovalConsumedError, ApprovalExpiredError, ApprovalRejectedError, ApprovalRequiredError, BlockedActionError, ExecutionError, InvalidToolInputError, ToolExecutionError, UnknownToolError

router = APIRouter()


class ApprovalCreate(BaseModel):
    tool_name: str
    input: dict[str, Any]
    reason: str = Field(min_length=1, max_length=500)


class ApprovalResolution(BaseModel):
    approved: bool


class ExecutionRequest(BaseModel):
    input: dict[str, Any]
    approval_id: UUID | None = None


def _executor(request: Request):
    return request.app.state.executor


def _authorize(request: Request, action: AuthorizationAction) -> AuthorizationContext:
    executor = _executor(request)
    try:
        identity = request.app.state.authenticator.authenticate(request.headers.get("Authorization"))
    except AuthenticationError as exc:
        executor.audit_security_event("authentication_failed", "denied")
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"}) from exc
    executor.audit_security_event("authentication_succeeded", "allowed", subject=identity.subject, role=identity.role.value)
    try:
        context = AuthorizationPolicy.authorize(identity, action)
    except AuthorizationError as exc:
        executor.audit_security_event("authorization_denied", "denied", subject=identity.subject, role=identity.role.value)
        raise HTTPException(status_code=403, detail="Not authorized") from exc
    executor.audit_security_event("authorization_allowed", "allowed", subject=identity.subject, role=identity.role.value)
    return context


def require(action: AuthorizationAction):
    def dependency(request: Request) -> AuthorizationContext:
        return _authorize(request, action)
    return dependency


def _http_error(exc: ExecutionError) -> HTTPException:
    if isinstance(exc, UnknownToolError): return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InvalidToolInputError): return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (ApprovalRequiredError, ApprovalRejectedError, ApprovalConsumedError, ApprovalExpiredError)): return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, BlockedActionError): return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ToolExecutionError): return HTTPException(status_code=500, detail="Tool execution failed")
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "jarvis-x"}


@router.get("/approvals", response_model=list[ApprovalRequest], tags=["approvals"])
async def list_approvals(request: Request, _: AuthorizationContext = Depends(require(AuthorizationAction.VIEW_APPROVALS))) -> tuple[ApprovalRequest, ...]:
    return _executor(request).approvals.list()


@router.get("/approvals/{approval_id}", response_model=ApprovalRequest, tags=["approvals"])
async def get_approval(approval_id: UUID, request: Request, _: AuthorizationContext = Depends(require(AuthorizationAction.VIEW_APPROVALS))) -> ApprovalRequest:
    try: return _executor(request).approvals.get(approval_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail="Approval request was not found") from exc


@router.post("/approvals", response_model=ApprovalRequest, status_code=201, tags=["approvals"])
async def create_approval(payload: ApprovalCreate, request: Request, context: AuthorizationContext = Depends(require(AuthorizationAction.CREATE_APPROVAL))) -> ApprovalRequest:
    try: return _executor(request).create_approval(payload.tool_name, payload.input, payload.reason, context.actor_id, context)
    except AuthorizationError as exc: raise HTTPException(status_code=403, detail="Not authorized") from exc
    except ExecutionError as exc: raise _http_error(exc) from exc


@router.post("/approvals/{approval_id}/resolve", response_model=ApprovalRequest, tags=["approvals"])
async def resolve_approval(approval_id: UUID, payload: ApprovalResolution, request: Request, context: AuthorizationContext = Depends(require(AuthorizationAction.RESOLVE_APPROVAL))) -> ApprovalRequest:
    try: return _executor(request).resolve_approval(approval_id, payload.approved, context)
    except KeyError as exc: raise HTTPException(status_code=404, detail="Approval request was not found") from exc
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/actions/{tool_name}/execute", tags=["actions"])
async def execute_action(tool_name: str, payload: ExecutionRequest, request: Request, context: AuthorizationContext = Depends(require(AuthorizationAction.EXECUTE))) -> dict[str, object]:
    try: return await _executor(request).execute(tool_name, payload.input, payload.approval_id, context)
    except AuthorizationError as exc: raise HTTPException(status_code=403, detail="Not authorized") from exc
    except ExecutionError as exc: raise _http_error(exc) from exc


@router.get("/audit-events", response_model=list[AuditEvent], tags=["audit"])
async def list_audit_events(request: Request, limit: int = Query(default=100, ge=1, le=100), _: AuthorizationContext = Depends(require(AuthorizationAction.VIEW_AUDIT))) -> tuple[AuditEvent, ...]:
    return _executor(request).repository.list_audit_events(limit)
