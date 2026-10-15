"""Controlled desktop screen capture tool requiring explicit confirmation."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.desktop.inspector import DesktopInspector
from app.desktop.screen import (
    DesktopScreenController,
    InvalidDisplayError,
    ScreenCaptureError,
    create_default_desktop_screen_controller,
)
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import InvalidToolInputError, ToolExecutionError


class DesktopScreenCaptureInput(BaseModel):
    display_id: str | None = Field(
        default=None,
        description="Optional display ID (e.g. 'display-1') to capture. Defaults to primary display.",
    )
    all_displays: bool = Field(
        default=False,
        description="Whether to capture all connected displays combined.",
    )
    save_to_temp: bool = Field(
        default=False,
        description="Whether to save the screenshot to a controlled temporary file. Cleaned up on request.",
    )
    include_base64: bool = Field(
        default=False,
        description="Whether to include base64-encoded PNG image data in the response.",
    )


class DesktopScreenCaptureTool(Tool[DesktopScreenCaptureInput]):
    """Safely capture desktop screen image metadata. Requires explicit confirmation."""

    name = "desktop_screen_capture"
    description = "Capture screen metadata and image safely. Requires explicit user confirmation."
    input_model = DesktopScreenCaptureInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        controller: DesktopScreenController | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.controller = controller or create_default_desktop_screen_controller(inspector=self.inspector)

    async def execute(self, input_data: DesktopScreenCaptureInput) -> dict[str, object]:
        try:
            res = await self.controller.capture(
                display_id=input_data.display_id,
                all_displays=input_data.all_displays,
                save_to_temp=input_data.save_to_temp,
                include_base64=input_data.include_base64,
            )
            return res.model_dump()
        except InvalidDisplayError as exc:
            raise ToolExecutionError(f"Screen capture display parameter invalid: {exc}") from exc
        except ScreenCaptureError as exc:
            raise ToolExecutionError(f"Screen capture execution failed: {exc}") from exc
