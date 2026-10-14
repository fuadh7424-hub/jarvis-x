"""Controlled window move and resize tool requiring explicit user confirmation."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.desktop.actions import (
    DesktopActionController,
    TargetWindowNotFoundError,
    WindowBoundsError,
    create_default_desktop_action_controller,
    find_target_window,
    validate_move_resize_bounds,
)
from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import ToolExecutionError


class DesktopWindowMoveResizeInput(BaseModel):
    window_id: str = Field(min_length=1, max_length=100, description="Target window identifier (e.g. win-0x1001)")
    left: int | None = Field(default=None, description="New X coordinate for window top-left corner")
    top: int | None = Field(default=None, description="New Y coordinate for window top-left corner")
    width: int | None = Field(default=None, ge=200, description="New width in pixels (minimum 200px)")
    height: int | None = Field(default=None, ge=150, description="New height in pixels (minimum 150px)")

    @model_validator(mode="after")
    def validate_at_least_one(self) -> DesktopWindowMoveResizeInput:
        if self.left is None and self.top is None and self.width is None and self.height is None:
            raise ValueError("At least one coordinate or dimension (left, top, width, or height) must be provided")
        return self


class DesktopWindowMoveResizeTool(Tool[DesktopWindowMoveResizeInput]):
    """Move and/or resize an existing top-level window within display bounds safely with post-action verification."""

    name = "desktop_window_move_resize"
    description = "Move and/or resize an existing top-level window safely within monitor bounds. Requires explicit confirmation."
    input_model = DesktopWindowMoveResizeInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        controller: DesktopActionController | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.controller = controller or create_default_desktop_action_controller(inspector=self.inspector)

    async def execute(self, input_data: DesktopWindowMoveResizeInput) -> dict[str, object]:
        try:
            target = await find_target_window(self.inspector, input_data.window_id)
        except TargetWindowNotFoundError as exc:
            raise ToolExecutionError(f"Target window not found: {exc}") from exc

        try:
            nl, nt, nw, nh = await validate_move_resize_bounds(
                self.inspector,
                target,
                input_data.left,
                input_data.top,
                input_data.width,
                input_data.height,
            )
        except WindowBoundsError as exc:
            raise ToolExecutionError(f"Window bounds invalid: {exc}") from exc

        res = await self.controller.move_resize_window(target.window_id, nl, nt, nw, nh)
        if not res.verified or not res.success:
            raise ToolExecutionError(f"Window move/resize verification failed: {res.message}")
        return res.model_dump()
