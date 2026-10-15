"""Controlled mouse click tool requiring explicit user confirmation."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, model_validator

from app.desktop.input import (
    DesktopInputController,
    MouseClickAction,
    MouseCoordinatesOutOfBoundsError,
    create_default_desktop_input_controller,
)
from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import ToolExecutionError


class DesktopMouseClickInput(BaseModel):
    x: int | None = Field(default=None, description="Optional target X coordinate in pixels")
    y: int | None = Field(default=None, description="Optional target Y coordinate in pixels")
    click_type: MouseClickAction = Field(
        default="left_click",
        description="Safe mouse click action: 'left_click', 'right_click', or 'double_left_click'",
    )
    button: Literal["left", "right", "double_left"] | None = Field(
        default=None,
        description="Alias for click_type: 'left', 'right', or 'double_left'",
    )

    @model_validator(mode="after")
    def validate_click_params(self) -> DesktopMouseClickInput:
        if self.button is not None:
            btn_map = {
                "left": "left_click",
                "right": "right_click",
                "double_left": "double_left_click",
            }
            self.click_type = btn_map[self.button]

        if (self.x is None and self.y is not None) or (self.x is not None and self.y is None):
            raise ValueError("Both x and y coordinates must be provided together, or omitted to click at current position")
        return self


class DesktopMouseClickTool(Tool[DesktopMouseClickInput]):
    """Safely execute a mouse click (left, right, or double left) with post-action position verification."""

    name = "desktop_mouse_click"
    description = "Click mouse at validated screen coordinates or current cursor position safely. Requires explicit confirmation."
    input_model = DesktopMouseClickInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        controller: DesktopInputController | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.controller = controller or create_default_desktop_input_controller(inspector=self.inspector)

    async def execute(self, input_data: DesktopMouseClickInput) -> dict[str, object]:
        try:
            res = await self.controller.mouse_click(input_data.click_type, input_data.x, input_data.y)
        except MouseCoordinatesOutOfBoundsError as exc:
            raise ToolExecutionError(f"Mouse click coordinates invalid: {exc}") from exc

        if not res.verified or not res.success:
            raise ToolExecutionError(f"Mouse click verification failed: {res.message}")
        return res.model_dump()
