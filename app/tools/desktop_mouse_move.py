"""Controlled mouse movement tool requiring explicit user confirmation."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.desktop.input import (
    DesktopInputController,
    MouseCoordinatesOutOfBoundsError,
    create_default_desktop_input_controller,
)
from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import ToolExecutionError


class DesktopMouseMoveInput(BaseModel):
    x: int = Field(description="Target screen X coordinate in pixels")
    y: int = Field(description="Target screen Y coordinate in pixels")


class DesktopMouseMoveTool(Tool[DesktopMouseMoveInput]):
    """Safely move mouse cursor to validated screen coordinates with post-action position verification."""

    name = "desktop_mouse_move"
    description = "Move mouse cursor to validated screen coordinates safely. Requires explicit confirmation."
    input_model = DesktopMouseMoveInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        controller: DesktopInputController | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.controller = controller or create_default_desktop_input_controller(inspector=self.inspector)

    async def execute(self, input_data: DesktopMouseMoveInput) -> dict[str, object]:
        try:
            res = await self.controller.mouse_move(input_data.x, input_data.y)
        except MouseCoordinatesOutOfBoundsError as exc:
            raise ToolExecutionError(f"Mouse move coordinates invalid: {exc}") from exc

        if not res.verified or not res.success:
            raise ToolExecutionError(f"Mouse movement verification failed: {res.message}")
        return res.model_dump()
