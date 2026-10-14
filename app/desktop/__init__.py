"""Controlled desktop awareness and window state management subsystem."""
from app.desktop.actions import (
    DesktopActionController,
    DesktopActionResult,
    MockDesktopActionController,
    TargetWindowNotFoundError,
    WindowBoundsError,
    WindowsDesktopActionController,
    WindowStateOperation,
    create_default_desktop_action_controller,
    find_target_window,
    validate_move_resize_bounds,
)
from app.desktop.inspector import (
    DesktopDriver,
    DesktopInspector,
    MockDesktopDriver,
    WindowsDesktopDriver,
    sanitize_window_title,
)
from app.desktop.models import (
    ActiveWindow,
    DesktopWindow,
    DisplayBounds,
    DisplayInfo,
    WindowBounds,
)

__all__ = [
    "ActiveWindow",
    "DesktopActionController",
    "DesktopActionResult",
    "DesktopDriver",
    "DesktopInspector",
    "DesktopWindow",
    "DisplayBounds",
    "DisplayInfo",
    "MockDesktopActionController",
    "MockDesktopDriver",
    "TargetWindowNotFoundError",
    "WindowBounds",
    "WindowBoundsError",
    "WindowsDesktopActionController",
    "WindowStateOperation",
    "create_default_desktop_action_controller",
    "find_target_window",
    "sanitize_window_title",
    "validate_move_resize_bounds",
]
