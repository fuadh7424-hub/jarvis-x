"""Authorization boundary kept separate from permission and approval policy."""
from dataclasses import dataclass


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class AuthorizationContext:
    actor_id: str
    may_request_approval: bool = True
    may_resolve_approval: bool = True
    may_execute: bool = True


class AuthorizationPolicy:
    """Future authentication supplies this context; executor need not change."""

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
