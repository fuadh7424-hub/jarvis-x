"""Controlled browser text typing tool requiring explicit user confirmation and sensitive data blocking."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.browser.actions import BrowserActionController, ElementTarget, validate_non_sensitive_text
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class BrowserTypeInput(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="Non-sensitive text to type into target element")
    target_selector: str | None = Field(default=None, max_length=200, description="CSS selector for target element")
    target_id: str | None = Field(default=None, max_length=100, description="HTML id of target element")
    target_role: str | None = Field(default=None, max_length=50, description="Accessible role of target element")
    target_name: str | None = Field(default=None, max_length=100, description="Accessible label or text of target element")
    port: int = Field(default=9222, ge=1024, le=65535, description="Local browser debugging port")

    @model_validator(mode="after")
    def validate_target_and_content(self) -> BrowserTypeInput:
        target = ElementTarget(
            selector=self.target_selector,
            element_id=self.target_id,
            role=self.target_role,
            name=self.target_name,
        )
        if not target.has_target():
            raise ValueError("At least one target identifier (target_selector, target_id, target_role, or target_name) must be provided")

        # Validate that text does not contain passwords, secrets, or tokens
        validate_non_sensitive_text(self.text, target)
        return self


class BrowserTypeTool(Tool[BrowserTypeInput]):
    """Type non-sensitive text into a validated target element without exposing credentials or logging raw text."""

    name = "browser_type"
    description = "Type non-sensitive text into a validated target element. Requires explicit confirmation."
    input_model = BrowserTypeInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(self, controller: BrowserActionController | None = None) -> None:
        self.controller = controller or BrowserActionController()

    async def execute(self, input_data: BrowserTypeInput) -> dict[str, object]:
        target = ElementTarget(
            selector=input_data.target_selector,
            element_id=input_data.target_id,
            role=input_data.target_role,
            name=input_data.target_name,
        )
        res = await self.controller.type_text(target, input_data.text, port=input_data.port)
        return res.model_dump()
