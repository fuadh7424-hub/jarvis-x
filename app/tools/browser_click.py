"""Controlled browser element click tool requiring explicit user confirmation."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.browser.actions import BrowserActionController, ElementTarget
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class BrowserClickInput(BaseModel):
    target_selector: str | None = Field(default=None, max_length=200, description="CSS selector for target element")
    target_id: str | None = Field(default=None, max_length=100, description="HTML id of target element")
    target_role: str | None = Field(default=None, max_length=50, description="Accessible role of target element")
    target_name: str | None = Field(default=None, max_length=100, description="Accessible label or text of target element")
    port: int = Field(default=9222, ge=1024, le=65535, description="Local browser debugging port")

    @model_validator(mode="after")
    def validate_target(self) -> BrowserClickInput:
        target = ElementTarget(
            selector=self.target_selector,
            element_id=self.target_id,
            role=self.target_role,
            name=self.target_name,
        )
        if not target.has_target():
            raise ValueError("At least one target identifier (target_selector, target_id, target_role, or target_name) must be provided")
        return self


class BrowserClickTool(Tool[BrowserClickInput]):
    """Click a validated target on the current page without screen coordinates or arbitrary JavaScript injection."""

    name = "browser_click"
    description = "Click a validated target element on the current page. Requires explicit confirmation."
    input_model = BrowserClickInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(self, controller: BrowserActionController | None = None) -> None:
        self.controller = controller or BrowserActionController()

    async def execute(self, input_data: BrowserClickInput) -> dict[str, object]:
        target = ElementTarget(
            selector=input_data.target_selector,
            element_id=input_data.target_id,
            role=input_data.target_role,
            name=input_data.target_name,
        )
        res = await self.controller.click(target, port=input_data.port)
        return res.model_dump()
