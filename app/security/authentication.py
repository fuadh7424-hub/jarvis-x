"""Replaceable local bearer-token authentication."""
from __future__ import annotations

import hmac
from collections.abc import Sequence

from app.core.config import AuthTokenConfig
from app.security.access import Identity


class AuthenticationError(PermissionError):
    pass


class BearerTokenAuthenticator:
    """Environment-configured tokens for a single-host installation."""

    def __init__(self, credentials: Sequence[AuthTokenConfig]) -> None:
        self._credentials = tuple(credentials)

    def authenticate(self, authorization_header: str | None) -> Identity:
        if not authorization_header:
            raise AuthenticationError("Authentication failed")
        parts = authorization_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise AuthenticationError("Authentication failed")
        token = parts[1].strip()
        matches = [credential for credential in self._credentials if hmac.compare_digest(credential.token.get_secret_value(), token)]
        if len(matches) != 1:
            raise AuthenticationError("Authentication failed")
        credential = matches[0]
        return Identity(subject=credential.subject, role=credential.role, authenticated=True)
