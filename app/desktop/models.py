"""Data models for controlled, read-only desktop awareness."""
from __future__ import annotations

from pydantic import BaseModel, Field


class WindowBounds(BaseModel):
    """Geometry of a window on screen."""
    left: int = Field(default=0, description="X coordinate of left edge")
    top: int = Field(default=0, description="Y coordinate of top edge")
    width: int = Field(default=0, description="Window width in pixels")
    height: int = Field(default=0, description="Window height in pixels")


class DesktopWindow(BaseModel):
    """Safe metadata for a single visible top-level window."""
    window_id: str = Field(description="Stable window identifier")
    title: str = Field(description="Sanitized window title")
    process_id: int | None = Field(default=None, description="Owning process PID")
    process_name: str | None = Field(default=None, description="Owning process executable name")
    is_minimized: bool = Field(default=False, description="Whether window is minimized/iconic")
    bounds: WindowBounds | None = Field(default=None, description="Window position and size")


class ActiveWindow(BaseModel):
    """Metadata for the currently focused top-level window."""
    found: bool = Field(default=False, description="Whether an active window was detected")
    window_id: str | None = Field(default=None, description="Window identifier if found")
    title: str | None = Field(default=None, description="Sanitized window title")
    process_id: int | None = Field(default=None, description="Owning process PID")
    process_name: str | None = Field(default=None, description="Owning process executable name")
    is_minimized: bool = Field(default=False, description="Whether window is minimized")
    bounds: WindowBounds | None = Field(default=None, description="Window position and size")


class DisplayBounds(BaseModel):
    """Geometry of a display monitor."""
    left: int = Field(default=0, description="Left coordinate")
    top: int = Field(default=0, description="Top coordinate")
    right: int = Field(default=0, description="Right coordinate")
    bottom: int = Field(default=0, description="Bottom coordinate")
    width: int = Field(default=0, description="Display width in pixels")
    height: int = Field(default=0, description="Display height in pixels")


class DisplayInfo(BaseModel):
    """Safe metadata for a connected display/monitor."""
    id: str = Field(description="Display identifier, e.g. display-1")
    is_primary: bool = Field(default=False, description="Whether this is the primary display")
    bounds: DisplayBounds = Field(description="Full monitor coordinate bounding box")
    work_area: DisplayBounds = Field(description="Work area excluding taskbar/dock")
    scale_factor: float | None = Field(default=1.0, description="Display scaling factor if available")
