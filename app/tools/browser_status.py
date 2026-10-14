"""Deterministic, read-only browser status inspection tool."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.browser.inspector import BrowserInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class BrowserStatusInput(BaseModel):
    port: int = Field(default=9222, ge=1024, le=65535, description="Local debugging port to check for browser inspection")


class BrowserStatusTool(Tool[BrowserStatusInput]):
    """Report browser process availability and local inspection connection state without modifying browser state."""

    name = "browser_status"
    description = "Inspect whether supported browsers are running and check browser inspection connection."
    input_model = BrowserStatusInput
    permission_level = PermissionLevel.SAFE

    def __init__(self, inspector: BrowserInspector | None = None) -> None:
        self.inspector = inspector or BrowserInspector()

    async def execute(self, input_data: BrowserStatusInput) -> dict[str, object]:
        return await self.inspector.get_status(port=input_data.port)
