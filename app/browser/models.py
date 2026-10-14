"""Data models for controlled, read-only browser awareness."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class BrowserTab(BaseModel):
    """Safe metadata for a single browser tab."""
    id: str = Field(description="Unique tab identifier")
    title: str = Field(description="Sanitized page title")
    url: str | None = Field(default=None, description="Sanitized page URL")
    is_active: bool = Field(default=False, description="Whether this tab is currently active")
    browser: str = Field(default="unknown", description="Browser identifier")


class BrowserStatus(BaseModel):
    """High-level availability and connection status of supported browsers."""
    supported_browsers: list[str] = Field(default_factory=list)
    detected_browsers: list[str] = Field(default_factory=list)
    is_running: bool = False
    connected: bool = False
    browser_name: str | None = None
    browser_version: str | None = None
    inspection_endpoint: str | None = None
    message: str = ""


class PageMetadata(BaseModel):
    """Safe, high-level metadata for a browser page."""
    found: bool = False
    title: str | None = None
    url: str | None = None
    domain: str | None = None
    protocol: str | None = None
    is_secure: bool | None = None
    source: Literal["cdp", "window_inspection", "none"] = "none"
