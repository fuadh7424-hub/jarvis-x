"""Controlled window state tool (minimize, maximize, restore) requiring explicit user confirmation."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

from app.desktop.actions import (
    DesktopActionController,
    TargetWindowNotFoundError,
    WindowStateOperation,
    create_default_desktop_action_controller,
    find_target_window,
)
from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import ToolExecutionError


class DesktopWindowStateInput(BaseModel):
    window_id: str = Field(min_length=1, max_length=100, description="Target window identifier (e.g. win-0x1001)")
    operation: Literal["minimize", "maximize", "restore"] = Field(
        description="Window state change operation: 'minimize', 'maximize', or 'restore'"
    )


class DesktopWindowStateTool(Tool[DesktopWindowStateInput]):
    """Minimize, maximize, or restore a visible application window safely with post-action verification."""

    name = "desktop_window_state"
    description = "Minimize, maximize, or restore an existing top-level window safely. Requires explicit confirmation."
    input_model = DesktopWindowStateInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        controller: DesktopActionController | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.controller = controller or create_default_desktop_action_controller(inspector=self.inspector)

    async def execute(self, input_data: DesktopWindowStateInput) -> dict[str, object]:
        try:
            target = await find_target_window(self.inspector, input_data.window_id)
        except TargetWindowNotFoundError as exc:
            raise ToolExecutionError(f"Target window not found: {exc}") from exc

        res = await self.controller.set_window_state(target.window_id, input_data.operation)
        if not res.verified or not res.success:
            raise ToolExecutionError(f"Window state verification failed for '{input_data.operation}': {res.message}")
        return res.model_dump()
