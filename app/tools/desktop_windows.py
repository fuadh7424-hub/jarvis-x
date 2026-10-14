"""Deterministic, read-only desktop windows inspection tool."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class DesktopWindowsInput(BaseModel):
    limit: int = Field(default=50, ge=1, le=100, description="Maximum number of windows to return (1-100)")


class DesktopWindowsTool(Tool[DesktopWindowsInput]):
    """Enumerate visible desktop application windows with sanitized titles without modifying state."""

    name = "desktop_windows"
    description = "Inspect visible top-level application windows (titles, PIDs, process names, bounds, minimized state) safely."
    input_model = DesktopWindowsInput
    permission_level = PermissionLevel.SAFE

    def __init__(self, inspector: DesktopInspector | None = None) -> None:
        self.inspector = inspector or DesktopInspector()

    async def execute(self, input_data: DesktopWindowsInput) -> dict[str, object]:
        return await self.inspector.get_windows(limit=input_data.limit)
