"""Deterministic, read-only desktop display inspection tool."""
from __future__ import annotations

from pydantic import BaseModel

from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class DesktopDisplaysInput(BaseModel):
    """Input payload for desktop displays inspection; no parameters required."""
    pass


class DesktopDisplaysTool(Tool[DesktopDisplaysInput]):
    """Enumerate connected displays and resolutions without capturing screen pixels."""

    name = "desktop_displays"
    description = "Inspect connected displays, resolutions, bounds, and work areas safely without capturing screen pixels."
    input_model = DesktopDisplaysInput
    permission_level = PermissionLevel.SAFE

    def __init__(self, inspector: DesktopInspector | None = None) -> None:
        self.inspector = inspector or DesktopInspector()

    async def execute(self, input_data: DesktopDisplaysInput) -> dict[str, object]:
        return await self.inspector.get_displays()
