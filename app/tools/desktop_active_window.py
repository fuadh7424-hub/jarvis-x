"""Deterministic, read-only active desktop window inspection tool."""
from __future__ import annotations

from pydantic import BaseModel

from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class DesktopActiveWindowInput(BaseModel):
    """Input payload for active window inspection; no parameters required."""
    pass


class DesktopActiveWindowTool(Tool[DesktopActiveWindowInput]):
    """Inspect the currently focused desktop window without altering focus or window state."""

    name = "desktop_active_window"
    description = "Inspect the currently focused top-level window (title, PID, process name, bounds) safely."
    input_model = DesktopActiveWindowInput
    permission_level = PermissionLevel.SAFE

    def __init__(self, inspector: DesktopInspector | None = None) -> None:
        self.inspector = inspector or DesktopInspector()

    async def execute(self, input_data: DesktopActiveWindowInput) -> dict[str, object]:
        return await self.inspector.get_active_window()
