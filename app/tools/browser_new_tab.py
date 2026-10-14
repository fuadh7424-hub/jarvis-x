"""Controlled browser new tab creation tool requiring explicit user confirmation."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.browser.actions import BrowserActionController, validate_navigation_url
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class BrowserNewTabInput(BaseModel):
    url: str | None = Field(default=None, max_length=500, description="Optional destination URL (http or https)")
    port: int = Field(default=9222, ge=1024, le=65535, description="Local browser debugging port")

    @field_validator("url")
    @classmethod
    def validate_dest_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_navigation_url(v)


class BrowserNewTabTool(Tool[BrowserNewTabInput]):
    """Open a new browser tab with a validated destination URL only upon explicit user confirmation."""

    name = "browser_new_tab"
    description = "Open a new browser tab with an optional validated destination URL. Requires explicit confirmation."
    input_model = BrowserNewTabInput
    permission_level = PermissionLevel.CONFIRM

    def __init__(self, controller: BrowserActionController | None = None) -> None:
        self.controller = controller or BrowserActionController()

    async def execute(self, input_data: BrowserNewTabInput) -> dict[str, object]:
        res = await self.controller.new_tab(input_data.url, port=input_data.port)
        return res.model_dump()
