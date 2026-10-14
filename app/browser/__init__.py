"""Controlled browser awareness and action execution for JARVIS-X."""
from app.browser.actions import (
    BrowserActionController,
    BrowserActionResult,
    BrowserDriver,
    ElementTarget,
    validate_navigation_url,
    validate_non_sensitive_text,
)
from app.browser.inspector import BrowserInspector
from app.browser.models import BrowserStatus, BrowserTab, PageMetadata

__all__ = [
    "BrowserActionController",
    "BrowserActionResult",
    "BrowserDriver",
    "BrowserInspector",
    "BrowserStatus",
    "BrowserTab",
    "ElementTarget",
    "PageMetadata",
    "validate_navigation_url",
    "validate_non_sensitive_text",
]
