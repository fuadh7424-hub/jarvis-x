"""Deterministic, read-only active browser tab inspection tool."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.browser.inspector import BrowserInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class ActiveTabInput(BaseModel):
    port: int = Field(default=9222, ge=1024, le=65535, description="Local debugging port to inspect")


class ActiveTabTool(Tool[ActiveTabInput]):
    """Return URL and title for the active browser tab without modifying state."""

    name = "active_tab"
    description = "Inspect the active browser tab URL and title safely."
    input_model = ActiveTabInput
    permission_level = PermissionLevel.SAFE

    def __init__(self, inspector: BrowserInspector | None = None) -> None:
        self.inspector = inspector or BrowserInspector()

    async def execute(self, input_data: ActiveTabInput) -> dict[str, object]:
        return await self.inspector.get_active_tab(port=input_data.port)
