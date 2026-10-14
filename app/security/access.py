"""Typed identity and centralized role-based authorization."""
from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    OWNER = "owner"
    OPERATOR = "operator"
    AUDITOR = "auditor"
    READ_ONLY = "read_only"


class AuthorizationAction(str, Enum):
    VIEW_APPROVALS = "view_approvals"
    CREATE_APPROVAL = "create_approval"
    RESOLVE_APPROVAL = "resolve_approval"
    EXECUTE = "execute"
    VIEW_AUDIT = "view_audit"


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class Identity:
    subject: str
    role: Role
    authenticated: bool


@dataclass(frozen=True)
class AuthorizationContext:
    actor_id: str
    role: Role | None = None
    authenticated: bool = False
    may_request_approval: bool = True
    may_resolve_approval: bool = True
    may_execute: bool = True


class AuthorizationPolicy:
    """One policy table for all protected API operations."""

    _permissions = {
        Role.OWNER: frozenset(AuthorizationAction),
        Role.OPERATOR: frozenset({AuthorizationAction.VIEW_APPROVALS, AuthorizationAction.CREATE_APPROVAL, AuthorizationAction.EXECUTE, AuthorizationAction.VIEW_AUDIT}),
        Role.AUDITOR: frozenset({AuthorizationAction.VIEW_APPROVALS, AuthorizationAction.VIEW_AUDIT}),
        Role.READ_ONLY: frozenset(),
    }

    @classmethod
    def authorize(cls, identity: Identity, action: AuthorizationAction) -> AuthorizationContext:
        if not identity.authenticated:
            raise AuthorizationError("Authentication is required")
        if action not in cls._permissions[identity.role]:
            raise AuthorizationError("Identity is not authorized for this operation")
        return AuthorizationContext(
            actor_id=identity.subject, role=identity.role, authenticated=True,
            may_request_approval=action is AuthorizationAction.CREATE_APPROVAL,
            may_resolve_approval=action is AuthorizationAction.RESOLVE_APPROVAL,
            may_execute=action is AuthorizationAction.EXECUTE,
        )

    @staticmethod
    def require_request(context: AuthorizationContext) -> None:
        if not context.may_request_approval:
            raise AuthorizationError("Actor may not request approvals")

    @staticmethod
    def require_resolution(context: AuthorizationContext) -> None:
        if not context.may_resolve_approval:
            raise AuthorizationError("Actor may not resolve approvals")

    @staticmethod
    def require_execution(context: AuthorizationContext) -> None:
        if not context.may_execute:
            raise AuthorizationError("Actor may not execute actions")
