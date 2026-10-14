"""Controlled browser navigation tool requiring explicit user confirmation."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.browser.actions import BrowserActionController, validate_navigation_url
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class BrowserNavigateInput(BaseModel):
    url: str = Field(description="Target URL to navigate to (http or https only)")
    port: int = Field(default=9222, ge=1024, le=65535, description="Local browser debugging port")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return validate_navigation_url(v)


class BrowserNavigateTool(Tool[BrowserNavigateInput]):
    """Navigate current browser tab to a validated destination URL with deterministic verification."""

    name = "browser_navigate"
    description = "Navigate browser to a validated HTTP/HTTPS URL. Requires explicit confirmation."
    input_model = BrowserNavigateInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(self, controller: BrowserActionController | None = None) -> None:
        self.controller = controller or BrowserActionController()

    async def execute(self, input_data: BrowserNavigateInput) -> dict[str, object]:
        res = await self.controller.navigate(input_data.url, port=input_data.port)
        return res.model_dump()
