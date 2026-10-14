"""Deterministic, read-only browser tabs inspection tool."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.browser.inspector import BrowserInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class BrowserTabsInput(BaseModel):
    limit: int = Field(default=50, ge=1, le=100, description="Maximum number of tabs to return (1-100)")
    port: int = Field(default=9222, ge=1024, le=65535, description="Local debugging port to inspect")


class BrowserTabsTool(Tool[BrowserTabsInput]):
    """Return safe metadata for open browser tabs without exposing sensitive session or profile data."""

    name = "browser_tabs"
    description = "Inspect open browser tabs (titles, sanitized URLs, active state) safely without modifying browser state."
    input_model = BrowserTabsInput
    permission_level = PermissionLevel.SAFE

    def __init__(self, inspector: BrowserInspector | None = None) -> None:
        self.inspector = inspector or BrowserInspector()

    async def execute(self, input_data: BrowserTabsInput) -> dict[str, object]:
        return await self.inspector.get_tabs(limit=input_data.limit, port=input_data.port)
