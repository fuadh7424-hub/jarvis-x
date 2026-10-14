"""Deterministic, read-only active page metadata inspection tool."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.browser.inspector import BrowserInspector
from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class PageMetadataInput(BaseModel):
    port: int = Field(default=9222, ge=1024, le=65535, description="Local debugging port to inspect")


class PageMetadataTool(Tool[PageMetadataInput]):
    """Return high-level metadata (domain, protocol, title, security status) for the active page."""

    name = "page_metadata"
    description = "Inspect safe high-level metadata (title, sanitized URL, domain, protocol) of the active page."
    input_model = PageMetadataInput
    permission_level = PermissionLevel.SAFE

    def __init__(self, inspector: BrowserInspector | None = None) -> None:
        self.inspector = inspector or BrowserInspector()

    async def execute(self, input_data: PageMetadataInput) -> dict[str, object]:
        return await self.inspector.get_page_metadata(port=input_data.port)
