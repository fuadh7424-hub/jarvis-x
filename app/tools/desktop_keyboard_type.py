"""Controlled keyboard typing tool requiring explicit user confirmation and sensitive data blocking."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.desktop.input import (
    DesktopInputController,
    SensitiveInputError,
    create_default_desktop_input_controller,
    validate_desktop_text,
)
from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import ToolExecutionError


class DesktopKeyboardTypeInput(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="Exact non-sensitive text to type (max 500 characters)")

    @field_validator("text")
    @classmethod
    def validate_text(cls, val: str) -> str:
        return validate_desktop_text(val)


class DesktopKeyboardTypeTool(Tool[DesktopKeyboardTypeInput]):
    """Type non-sensitive text into the currently active desktop window safely without logging raw text."""

    name = "desktop_keyboard_type"
    description = "Type non-sensitive text into active window safely. Requires explicit confirmation."
    input_model = DesktopKeyboardTypeInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        controller: DesktopInputController | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.controller = controller or create_default_desktop_input_controller(inspector=self.inspector)

    async def execute(self, input_data: DesktopKeyboardTypeInput) -> dict[str, object]:
        try:
            res = await self.controller.keyboard_type(input_data.text)
        except (SensitiveInputError, ValueError) as exc:
            raise ToolExecutionError(f"Keyboard text input invalid: {exc}") from exc

        if not res.verified or not res.success:
            raise ToolExecutionError(f"Keyboard text entry verification failed: {res.message}")
        return res.model_dump()
