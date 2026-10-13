"""Base interface for every capability exposed to the agent."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.security.permissions import PermissionLevel

InputModel = TypeVar("InputModel", bound=BaseModel)


class Tool(ABC, Generic[InputModel]):
    name: str
    description: str
    input_model: type[InputModel]
    permission_level: PermissionLevel

    @abstractmethod
    async def execute(self, input_data: InputModel) -> dict[str, object]:
        """Execute a controlled capability. Implementations must not bypass policy."""

