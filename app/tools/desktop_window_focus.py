"""Controlled window focus tool requiring explicit user confirmation."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.desktop.actions import (
    DesktopActionController,
    TargetWindowNotFoundError,
    create_default_desktop_action_controller,
    find_target_window,
)
from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import ToolExecutionError


class DesktopWindowFocusInput(BaseModel):
    window_id: str = Field(min_length=1, max_length=100, description="Target window identifier (e.g. win-0x1001)")


class DesktopWindowFocusTool(Tool[DesktopWindowFocusInput]):
    """Focus and activate an existing visible top-level window safely with post-action verification."""

    name = "desktop_window_focus"
    description = "Focus and activate an existing visible top-level window safely. Requires explicit confirmation."
    input_model = DesktopWindowFocusInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        controller: DesktopActionController | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.controller = controller or create_default_desktop_action_controller(inspector=self.inspector)

    async def execute(self, input_data: DesktopWindowFocusInput) -> dict[str, object]:
        try:
            target = await find_target_window(self.inspector, input_data.window_id)
        except TargetWindowNotFoundError as exc:
            raise ToolExecutionError(f"Target window not found: {exc}") from exc

        res = await self.controller.focus_window(target.window_id)
        if not res.verified or not res.success:
            raise ToolExecutionError(f"Window focus verification failed: {res.message}")
        return res.model_dump()
