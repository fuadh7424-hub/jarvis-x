"""Controlled hotkey execution tool with strict allowlist and explicit user confirmation."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field, field_validator

from app.desktop.input import (
    DesktopInputController,
    InvalidHotkeyError,
    SafeHotkey,
    create_default_desktop_input_controller,
    validate_safe_hotkey,
)
from app.desktop.inspector import DesktopInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool
from app.tools.executor import ToolExecutionError


class DesktopHotkeyInput(BaseModel):
    hotkey: SafeHotkey = Field(
        description="Allowlisted safe hotkey shortcut (e.g. CTRL+C, CTRL+V, ALT+TAB, ENTER, ESC)"
    )

    @field_validator("hotkey", mode="before")
    @classmethod
    def normalize_and_validate(cls, val: Any) -> str:
        if not isinstance(val, str):
            raise ValueError("Hotkey must be a string")
        return validate_safe_hotkey(val)


class DesktopHotkeyTool(Tool[DesktopHotkeyInput]):
    """Safely trigger an allowlisted desktop keyboard shortcut with post-action verification."""

    name = "desktop_hotkey"
    description = "Execute an allowlisted safe keyboard shortcut safely. Requires explicit confirmation."
    input_model = DesktopHotkeyInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        controller: DesktopInputController | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.controller = controller or create_default_desktop_input_controller(inspector=self.inspector)

    async def execute(self, input_data: DesktopHotkeyInput) -> dict[str, object]:
        try:
            res = await self.controller.press_hotkey(input_data.hotkey)
        except InvalidHotkeyError as exc:
            raise ToolExecutionError(f"Hotkey invalid: {exc}") from exc

        if not res.verified or not res.success:
            raise ToolExecutionError(f"Hotkey execution verification failed: {res.message}")
        return res.model_dump()
