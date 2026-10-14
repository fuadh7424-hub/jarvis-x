"""Controlled desktop action controller, window state management, and safety boundaries."""
from __future__ import annotations

import sys
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.desktop.inspector import DesktopInspector
from app.desktop.models import DesktopWindow, WindowBounds


WindowStateOperation = Literal["minimize", "maximize", "restore"]


class TargetWindowNotFoundError(ValueError):
    """Raised when the target window does not exist or is not visible."""
    pass


class WindowBoundsError(ValueError):
    """Raised when coordinates fall outside display work areas or violate dimensions."""
    pass


class DesktopActionResult(BaseModel):
    """Structured result of a controlled desktop action."""
    action: str = Field(description="Action identifier")
    success: bool = Field(description="Whether the action succeeded")
    verified: bool = Field(description="Whether post-action verification confirmed the change")
    window_id: str = Field(description="Target window identifier")
    message: str = Field(default="", description="Descriptive result or error message")
    details: dict[str, Any] = Field(default_factory=dict, description="Metadata or geometric details")


async def find_target_window(inspector: DesktopInspector, window_id: str) -> DesktopWindow:
    """Validate that the target window exists in the current desktop window enumeration."""
    clean_id = window_id.strip()
    res = await inspector.get_windows(limit=100)
    windows_data = res.get("windows", [])
    for w in windows_data:
        if isinstance(w, dict) and w.get("window_id") == clean_id:
            return DesktopWindow.model_validate(w)
        if isinstance(w, DesktopWindow) and w.window_id == clean_id:
            return w
    raise TargetWindowNotFoundError(f"Target window not found or not visible: {clean_id}")


async def validate_move_resize_bounds(
    inspector: DesktopInspector,
    target_window: DesktopWindow,
    left: int | None,
    top: int | None,
    width: int | None,
    height: int | None,
) -> tuple[int, int, int, int]:
    """Validate and resolve target window geometry within monitor work areas."""
    if target_window.is_minimized:
        raise WindowBoundsError("Cannot move or resize a minimized window. Restore it first.")

    current_bounds = target_window.bounds or WindowBounds(left=0, top=0, width=800, height=600)

    new_left = left if left is not None else current_bounds.left
    new_top = top if top is not None else current_bounds.top
    new_width = width if width is not None else current_bounds.width
    new_height = height if height is not None else current_bounds.height

    if new_width < 200:
        raise WindowBoundsError(f"Window width must be at least 200px (requested {new_width}px)")
    if new_height < 150:
        raise WindowBoundsError(f"Window height must be at least 150px (requested {new_height}px)")

    # Fetch displays to validate bounds
    displays_res = await inspector.get_displays()
    displays = displays_res.get("displays", [])

    if displays:
        # Check against work area boundaries
        valid_display = False
        for d in displays:
            wa = d.get("work_area") or d.get("bounds", {})
            wa_left = wa.get("left", 0)
            wa_top = wa.get("top", 0)
            wa_right = wa.get("right", wa_left + wa.get("width", 1920))
            wa_bottom = wa.get("bottom", wa_top + wa.get("height", 1080))

            # The top-left corner must be within work area or window intersects significantly
            if (
                wa_left <= new_left < wa_right
                and wa_top <= new_top < wa_bottom
                and new_left + 100 <= wa_right
                and new_top + 50 <= wa_bottom
            ):
                valid_display = True
                break

        if not valid_display:
            raise WindowBoundsError(
                f"Window position ({new_left}, {new_top}, {new_width}, {new_height}) "
                f"falls outside all available display work areas"
            )

    return new_left, new_top, new_width, new_height


class DesktopActionController(Protocol):
    """Protocol for controlled desktop window manipulation."""

    async def focus_window(self, window_id: str) -> DesktopActionResult: ...
    async def set_window_state(self, window_id: str, operation: WindowStateOperation) -> DesktopActionResult: ...
    async def move_resize_window(
        self, window_id: str, left: int, top: int, width: int, height: int
    ) -> DesktopActionResult: ...


class WindowsDesktopActionController:
    """Controlled desktop action controller using Windows User32 via ctypes."""

    def __init__(self, inspector: DesktopInspector | None = None) -> None:
        self.inspector = inspector or DesktopInspector()

    def _parse_hwnd(self, window_id: str) -> int:
        clean = window_id.strip()
        if clean.startswith("win-"):
            clean = clean[4:]
        try:
            return int(clean, 16) if clean.startswith("0x") else int(clean)
        except ValueError as exc:
            raise ValueError(f"Invalid window ID format: {window_id}") from exc

    async def focus_window(self, window_id: str) -> DesktopActionResult:
        hwnd = self._parse_hwnd(window_id)
        try:
            import ctypes

            user32 = ctypes.windll.user32
            if not user32.IsWindow(hwnd):
                return DesktopActionResult(
                    action="desktop_window_focus",
                    success=False,
                    verified=False,
                    window_id=window_id,
                    message="Window does not exist",
                )

            # If minimized, restore first
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE

            user32.SetForegroundWindow(hwnd)

            # Post-action verification
            fg = user32.GetForegroundWindow()
            verified = fg == hwnd
            return DesktopActionResult(
                action="desktop_window_focus",
                success=True,
                verified=verified,
                window_id=window_id,
                message="Window focused successfully" if verified else "Foreground window does not match target window",
            )
        except Exception as exc:
            return DesktopActionResult(
                action="desktop_window_focus",
                success=False,
                verified=False,
                window_id=window_id,
                message=str(exc),
            )

    async def set_window_state(self, window_id: str, operation: WindowStateOperation) -> DesktopActionResult:
        hwnd = self._parse_hwnd(window_id)
        try:
            import ctypes

            user32 = ctypes.windll.user32
            if not user32.IsWindow(hwnd):
                return DesktopActionResult(
                    action=f"desktop_window_state_{operation}",
                    success=False,
                    verified=False,
                    window_id=window_id,
                    message="Window does not exist",
                )

            cmd_map = {
                "minimize": 6,  # SW_MINIMIZE
                "maximize": 3,  # SW_MAXIMIZE
                "restore": 9,  # SW_RESTORE
            }
            cmd = cmd_map[operation]
            user32.ShowWindow(hwnd, cmd)

            # Post-action verification
            is_minimized = bool(user32.IsIconic(hwnd))
            if operation == "minimize":
                verified = is_minimized
            elif operation == "restore":
                verified = not is_minimized
            elif operation == "maximize":
                verified = bool(user32.IsZoomed(hwnd)) if hasattr(user32, "IsZoomed") else not is_minimized
            else:
                verified = False

            return DesktopActionResult(
                action=f"desktop_window_state_{operation}",
                success=True,
                verified=verified,
                window_id=window_id,
                message=f"Window state set to {operation}" if verified else f"Failed to verify window state {operation}",
            )
        except Exception as exc:
            return DesktopActionResult(
                action=f"desktop_window_state_{operation}",
                success=False,
                verified=False,
                window_id=window_id,
                message=str(exc),
            )

    async def move_resize_window(
        self, window_id: str, left: int, top: int, width: int, height: int
    ) -> DesktopActionResult:
        hwnd = self._parse_hwnd(window_id)
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            if not user32.IsWindow(hwnd):
                return DesktopActionResult(
                    action="desktop_window_move_resize",
                    success=False,
                    verified=False,
                    window_id=window_id,
                    message="Window does not exist",
                )

            # SWP_NOZORDER (0x0004) | SWP_NOACTIVATE (0x0010)
            user32.SetWindowPos(hwnd, 0, left, top, width, height, 0x0004 | 0x0010)

            # Post-action verification via GetWindowRect
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            actual_w = rect.right - rect.left
            actual_h = rect.bottom - rect.top

            # Allow small margin of error (±15px) for Windows DWM window borders and drop shadows
            pos_ok = abs(rect.left - left) <= 15 and abs(rect.top - top) <= 15
            size_ok = abs(actual_w - width) <= 15 and abs(actual_h - height) <= 15
            verified = pos_ok and size_ok

            return DesktopActionResult(
                action="desktop_window_move_resize",
                success=True,
                verified=verified,
                window_id=window_id,
                message="Window moved and resized successfully"
                if verified
                else f"Actual geometry ({rect.left}, {rect.top}, {actual_w}, {actual_h}) deviates from requested ({left}, {top}, {width}, {height})",
                details={"left": rect.left, "top": rect.top, "width": actual_w, "height": actual_h},
            )
        except Exception as exc:
            return DesktopActionResult(
                action="desktop_window_move_resize",
                success=False,
                verified=False,
                window_id=window_id,
                message=str(exc),
            )


class MockDesktopActionController:
    """Mock action controller for deterministic unit testing."""

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        simulate_verification_failure: bool = False,
    ) -> None:
        self.inspector = inspector
        self.simulate_verification_failure = simulate_verification_failure
        self.focused_window_id: str | None = None
        self.window_states: dict[str, str] = {}
        self.window_geometries: dict[str, WindowBounds] = {}

    async def focus_window(self, window_id: str) -> DesktopActionResult:
        if self.simulate_verification_failure:
            return DesktopActionResult(
                action="desktop_window_focus",
                success=True,
                verified=False,
                window_id=window_id,
                message="Simulated verification failure for window focus",
            )
        self.focused_window_id = window_id
        if self.inspector and hasattr(self.inspector._driver, "_active_window"):
            from app.desktop.models import ActiveWindow
            self.inspector._driver._active_window = ActiveWindow(
                found=True,
                window_id=window_id,
                title=f"Focused - {window_id}",
                process_id=1000,
                process_name="app.exe",
                is_minimized=False,
                bounds=WindowBounds(left=100, top=100, width=800, height=600),
            )
        return DesktopActionResult(
            action="desktop_window_focus",
            success=True,
            verified=True,
            window_id=window_id,
            message="Window focused successfully",
        )

    async def set_window_state(self, window_id: str, operation: WindowStateOperation) -> DesktopActionResult:
        if self.simulate_verification_failure:
            return DesktopActionResult(
                action=f"desktop_window_state_{operation}",
                success=True,
                verified=False,
                window_id=window_id,
                message=f"Simulated verification failure for window state {operation}",
            )
        self.window_states[window_id] = operation
        if self.inspector and hasattr(self.inspector._driver, "_windows"):
            for w in self.inspector._driver._windows:
                if w.window_id == window_id:
                    w.is_minimized = operation == "minimize"
                    break
        return DesktopActionResult(
            action=f"desktop_window_state_{operation}",
            success=True,
            verified=True,
            window_id=window_id,
            message=f"Window state set to {operation}",
        )

    async def move_resize_window(
        self, window_id: str, left: int, top: int, width: int, height: int
    ) -> DesktopActionResult:
        if self.simulate_verification_failure:
            return DesktopActionResult(
                action="desktop_window_move_resize",
                success=True,
                verified=False,
                window_id=window_id,
                message="Simulated verification failure for window move/resize",
            )
        bounds = WindowBounds(left=left, top=top, width=width, height=height)
        self.window_geometries[window_id] = bounds
        if self.inspector and hasattr(self.inspector._driver, "_windows"):
            for w in self.inspector._driver._windows:
                if w.window_id == window_id:
                    w.bounds = bounds
                    break
        return DesktopActionResult(
            action="desktop_window_move_resize",
            success=True,
            verified=True,
            window_id=window_id,
            message="Window moved and resized successfully",
            details={"left": left, "top": top, "width": width, "height": height},
        )


def create_default_desktop_action_controller(
    inspector: DesktopInspector | None = None,
) -> DesktopActionController:
    """Create default platform action controller."""
    if sys.platform == "win32":
        return WindowsDesktopActionController(inspector=inspector)
    return MockDesktopActionController(inspector=inspector)
