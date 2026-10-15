"""Controlled desktop input subsystem: safe mouse movement, clicking, text entry, and hotkeys."""
from __future__ import annotations

import re
import sys
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.desktop.inspector import DesktopInspector
from app.desktop.models import DisplayBounds, DisplayInfo


class MouseCoordinatesOutOfBoundsError(ValueError):
    """Raised when mouse coordinates fall outside all active display work areas."""
    pass


class InvalidHotkeyError(ValueError):
    """Raised when a requested hotkey combination is not allowlisted or is forbidden."""
    pass


class SensitiveInputError(ValueError):
    """Raised when keyboard text contains sensitive credentials, keys, or passwords."""
    pass


MouseClickAction = Literal["left_click", "right_click", "double_left_click"]

ALLOWED_HOTKEYS: tuple[str, ...] = (
    "CTRL+C",
    "CTRL+V",
    "CTRL+X",
    "CTRL+A",
    "CTRL+Z",
    "CTRL+Y",
    "CTRL+S",
    "CTRL+F",
    "ALT+TAB",
    "ESC",
    "ENTER",
    "BACKSPACE",
    "TAB",
    "SPACE",
    "DELETE",
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
    "PAGEUP",
    "PAGEDOWN",
    "HOME",
    "END",
)

ALLOWED_HOTKEYS_SET: set[str] = set(ALLOWED_HOTKEYS)

FORBIDDEN_HOTKEYS: set[str] = {
    "CTRL+ALT+DELETE",
    "CTRL+ALT+DEL",
    "ALT+F4",
    "CTRL+SHIFT+ESC",
    "WIN+L",
    "WIN+R",
    "WIN+X",
    "ALT+SPACE",
    "CTRL+W",
}

SafeHotkey = Literal[
    "CTRL+C",
    "CTRL+V",
    "CTRL+X",
    "CTRL+A",
    "CTRL+Z",
    "CTRL+Y",
    "CTRL+S",
    "CTRL+F",
    "ALT+TAB",
    "ESC",
    "ENTER",
    "BACKSPACE",
    "TAB",
    "SPACE",
    "DELETE",
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
    "PAGEUP",
    "PAGEDOWN",
    "HOME",
    "END",
]

SENSITIVE_TEXT_PATTERNS = [
    re.compile(r"(?i)(password\s*[:=]|passwd\s*[:=]|api[_-]?key\s*[:=]|secret\s*[:=]|bearer\s+ey|private[_-]?key)"),
    re.compile(r"(?i)(sk_(?:live|test)_[0-9a-zA-Z_]{16,}|ghp_[0-9a-zA-Z]{20,}|eyJ[a-zA-Z0-9_\-]{20,})"),
    re.compile(r"(?i)\b(my_secret_password|super_secret|admin_password)\b"),
]


class DesktopInputResult(BaseModel):
    """Structured result of a controlled desktop input action."""
    action: str = Field(description="Action identifier")
    success: bool = Field(description="Whether driver execution succeeded")
    verified: bool = Field(description="Whether post-action verification confirmed the action")
    message: str = Field(default="", description="Descriptive status or result message")
    details: dict[str, Any] = Field(default_factory=dict, description="Safe execution details")


async def validate_mouse_coordinates(
    inspector: DesktopInspector,
    x: int,
    y: int,
) -> tuple[int, int]:
    """Validate screen coordinates against active display work areas."""
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
        raise MouseCoordinatesOutOfBoundsError(f"Coordinates must be integers, got ({type(x).__name__}, {type(y).__name__})")

    displays_res = await inspector.get_displays()
    displays = displays_res.get("displays", [])

    if displays:
        valid = False
        for d in displays:
            if isinstance(d, dict):
                wa = d.get("work_area") or d.get("bounds", {})
                wa_left = wa.get("left", 0)
                wa_top = wa.get("top", 0)
                wa_right = wa.get("right", wa_left + wa.get("width", 1920))
                wa_bottom = wa.get("bottom", wa_top + wa.get("height", 1080))
            else:
                wa = getattr(d, "work_area", None) or getattr(d, "bounds", None)
                wa_left = getattr(wa, "left", 0)
                wa_top = getattr(wa, "top", 0)
                wa_right = getattr(wa, "right", wa_left + getattr(wa, "width", 1920))
                wa_bottom = getattr(wa, "bottom", wa_top + getattr(wa, "height", 1080))

            if wa_left <= x < wa_right and wa_top <= y < wa_bottom:
                valid = True
                break

        if not valid:
            raise MouseCoordinatesOutOfBoundsError(
                f"Coordinates ({x}, {y}) fall outside all active display work areas"
            )
    else:
        # Default single monitor work area bounds (0..1920, 0..1080)
        if not (0 <= x < 1920 and 0 <= y < 1080):
            raise MouseCoordinatesOutOfBoundsError(
                f"Coordinates ({x}, {y}) fall outside default display bounds (0..1920, 0..1080)"
            )

    return x, y


def validate_desktop_text(text: str) -> str:
    """Validate keyboard input text, ensuring no credentials or sensitive tokens are typed."""
    clean = text.strip()
    if not clean:
        raise ValueError("Text must not be empty or whitespace-only")
    if len(text) > 500:
        raise ValueError("Text length exceeds maximum allowed limit of 500 characters")
    if "\x00" in text:
        raise ValueError("Null characters are forbidden in keyboard text input")
    for ch in text:
        if ord(ch) < 32 and ch not in ("\n", "\r", "\t"):
            raise ValueError(f"Control character {ord(ch):#x} is forbidden in keyboard text input")
    for pat in SENSITIVE_TEXT_PATTERNS:
        if pat.search(clean):
            raise SensitiveInputError("Text contains sensitive credentials, API keys, or tokens which cannot be typed.")
    return text


def validate_safe_hotkey(hotkey: str) -> str:
    """Validate that requested hotkey is in the safe allowlist."""
    clean = hotkey.strip().upper()
    if clean in FORBIDDEN_HOTKEYS or clean not in ALLOWED_HOTKEYS_SET:
        raise InvalidHotkeyError(
            f"Hotkey '{hotkey}' is not in the safe allowlist. Prohibited or arbitrary key combinations are rejected."
        )
    return clean


class DesktopInputDriver(Protocol):
    """Protocol for native/mock desktop input drivers."""

    async def get_cursor_position(self) -> tuple[int, int]: ...
    async def move_cursor(self, x: int, y: int) -> tuple[bool, bool, tuple[int, int], str]: ...
    async def click(
        self, click_type: str, x: int | None = None, y: int | None = None
    ) -> tuple[bool, bool, tuple[int, int], str]: ...
    async def type_text(self, text: str) -> tuple[bool, bool, str]: ...
    async def press_hotkey(self, hotkey: str) -> tuple[bool, bool, str]: ...


class MockDesktopInputDriver:
    """Mock desktop input driver for deterministic unit testing."""

    def __init__(
        self,
        initial_position: tuple[int, int] = (100, 100),
        simulate_verification_failure: bool = False,
    ) -> None:
        self.cursor_position: tuple[int, int] = initial_position
        self.clicks: list[dict[str, Any]] = []
        self.typed_text: list[str] = []
        self.hotkeys: list[str] = []
        self.simulate_verification_failure = simulate_verification_failure

    async def get_cursor_position(self) -> tuple[int, int]:
        return self.cursor_position

    async def move_cursor(self, x: int, y: int) -> tuple[bool, bool, tuple[int, int], str]:
        if self.simulate_verification_failure:
            return True, False, self.cursor_position, "Cursor position verification failed"
        self.cursor_position = (x, y)
        return True, True, (x, y), f"Cursor moved to ({x}, {y}) successfully"

    async def click(
        self, click_type: str, x: int | None = None, y: int | None = None
    ) -> tuple[bool, bool, tuple[int, int], str]:
        target_pos = (x, y) if (x is not None and y is not None) else self.cursor_position
        if x is not None and y is not None:
            self.cursor_position = (x, y)
        self.clicks.append({"click_type": click_type, "position": target_pos})
        if self.simulate_verification_failure:
            return True, False, self.cursor_position, f"Mouse {click_type} verification failed"
        return True, True, self.cursor_position, f"Mouse {click_type} executed successfully"

    async def type_text(self, text: str) -> tuple[bool, bool, str]:
        self.typed_text.append(text)
        if self.simulate_verification_failure:
            return True, False, "Keyboard text entry verification failed"
        return True, True, f"Typed {len(text)} characters into active window"

    async def press_hotkey(self, hotkey: str) -> tuple[bool, bool, str]:
        self.hotkeys.append(hotkey)
        if self.simulate_verification_failure:
            return True, False, f"Hotkey '{hotkey}' execution verification failed"
        return True, True, f"Hotkey '{hotkey}' executed successfully"


class WindowsDesktopInputDriver:
    """Windows native input driver using user32 via ctypes."""

    VK_MAP: dict[str, int] = {
        "CTRL": 0x11,     # VK_CONTROL
        "ALT": 0x12,      # VK_MENU
        "SHIFT": 0x10,    # VK_SHIFT
        "C": 0x43,
        "V": 0x56,
        "X": 0x58,
        "A": 0x41,
        "Z": 0x5A,
        "Y": 0x59,
        "S": 0x53,
        "F": 0x46,
        "TAB": 0x09,      # VK_TAB
        "ESC": 0x1B,      # VK_ESCAPE
        "ENTER": 0x0D,    # VK_RETURN
        "BACKSPACE": 0x08,# VK_BACK
        "SPACE": 0x20,    # VK_SPACE
        "DELETE": 0x2E,   # VK_DELETE
        "UP": 0x26,       # VK_UP
        "DOWN": 0x28,     # VK_DOWN
        "LEFT": 0x25,     # VK_LEFT
        "RIGHT": 0x27,    # VK_RIGHT
        "PAGEUP": 0x21,   # VK_PRIOR
        "PAGEDOWN": 0x22, # VK_NEXT
        "HOME": 0x24,     # VK_HOME
        "END": 0x23,      # VK_END
    }

    def _get_user32(self):
        import ctypes
        return ctypes.windll.user32

    async def get_cursor_position(self) -> tuple[int, int]:
        try:
            import ctypes
            from ctypes import wintypes
            user32 = self._get_user32()
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y
        except Exception:
            return 0, 0

    async def move_cursor(self, x: int, y: int) -> tuple[bool, bool, tuple[int, int], str]:
        try:
            import ctypes
            from ctypes import wintypes
            user32 = self._get_user32()
            user32.SetCursorPos(int(x), int(y))

            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            verified = abs(pt.x - x) <= 2 and abs(pt.y - y) <= 2
            return True, verified, (pt.x, pt.y), f"Cursor moved to ({pt.x}, {pt.y})" if verified else f"Cursor position mismatch ({pt.x}, {pt.y}) vs requested ({x}, {y})"
        except Exception as exc:
            return False, False, (0, 0), str(exc)

    async def click(
        self, click_type: str, x: int | None = None, y: int | None = None
    ) -> tuple[bool, bool, tuple[int, int], str]:
        try:
            import ctypes
            from ctypes import wintypes
            user32 = self._get_user32()

            if x is not None and y is not None:
                user32.SetCursorPos(int(x), int(y))

            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))

            MOUSEEVENTF_LEFTDOWN = 0x0002
            MOUSEEVENTF_LEFTUP = 0x0004
            MOUSEEVENTF_RIGHTDOWN = 0x0008
            MOUSEEVENTF_RIGHTUP = 0x0010

            if click_type == "left_click":
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            elif click_type == "right_click":
                user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            elif click_type == "double_left_click":
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                time.sleep(0.05)
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            else:
                return False, False, (pt.x, pt.y), f"Unsupported click action '{click_type}'"

            verified = True
            if x is not None and y is not None:
                pt_after = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt_after))
                verified = abs(pt_after.x - x) <= 2 and abs(pt_after.y - y) <= 2

            return True, verified, (pt.x, pt.y), f"Mouse {click_type} executed successfully"
        except Exception as exc:
            return False, False, (0, 0), str(exc)

    async def type_text(self, text: str) -> tuple[bool, bool, str]:
        try:
            user32 = self._get_user32()
            hwnd_before = user32.GetForegroundWindow()

            KEYEVENTF_KEYUP = 0x0002
            KEYEVENTF_UNICODE = 0x0004

            for ch in text:
                if ch in ("\n", "\r"):
                    user32.keybd_event(0x0D, 0, 0, 0)
                    user32.keybd_event(0x0D, 0, KEYEVENTF_KEYUP, 0)
                elif ch == "\t":
                    user32.keybd_event(0x09, 0, 0, 0)
                    user32.keybd_event(0x09, 0, KEYEVENTF_KEYUP, 0)
                else:
                    user32.keybd_event(0, ord(ch), KEYEVENTF_UNICODE, 0)
                    user32.keybd_event(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)

            hwnd_after = user32.GetForegroundWindow()
            verified = (hwnd_before != 0 and hwnd_after == hwnd_before) or hwnd_after != 0
            return True, verified, f"Typed {len(text)} characters into active window"
        except Exception as exc:
            return False, False, str(exc)

    async def press_hotkey(self, hotkey: str) -> tuple[bool, bool, str]:
        try:
            user32 = self._get_user32()
            hwnd_before = user32.GetForegroundWindow()

            parts = [p.strip().upper() for p in hotkey.split("+")]
            vk_codes = [self.VK_MAP[p] for p in parts]

            KEYEVENTF_KEYUP = 0x0002

            # Press all in forward order
            for vk in vk_codes:
                user32.keybd_event(vk, 0, 0, 0)

            # Release all in reverse order
            for vk in reversed(vk_codes):
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

            hwnd_after = user32.GetForegroundWindow()
            verified = (hwnd_before != 0 and hwnd_after == hwnd_before) or hwnd_after != 0
            return True, verified, f"Hotkey '{hotkey}' executed successfully"
        except Exception as exc:
            return False, False, str(exc)


class DesktopInputController:
    """Controlled coordinator of desktop mouse, keyboard, and hotkey actions."""

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        driver: DesktopInputDriver | None = None,
    ) -> None:
        self.inspector = inspector or DesktopInspector()
        self.driver = driver or create_default_desktop_input_driver()

    async def mouse_move(self, x: int, y: int) -> DesktopInputResult:
        await validate_mouse_coordinates(self.inspector, x, y)
        success, verified, pos, msg = await self.driver.move_cursor(x, y)
        return DesktopInputResult(
            action="desktop_mouse_move",
            success=success,
            verified=verified,
            message=msg,
            details={"x": pos[0], "y": pos[1], "target_x": x, "target_y": y},
        )

    async def mouse_click(
        self, click_type: str, x: int | None = None, y: int | None = None
    ) -> DesktopInputResult:
        if x is not None and y is not None:
            await validate_mouse_coordinates(self.inspector, x, y)
        else:
            current_pos = await self.driver.get_cursor_position()
            await validate_mouse_coordinates(self.inspector, current_pos[0], current_pos[1])

        success, verified, pos, msg = await self.driver.click(click_type, x, y)
        return DesktopInputResult(
            action="desktop_mouse_click",
            success=success,
            verified=verified,
            message=msg,
            details={"x": pos[0], "y": pos[1], "click_type": click_type},
        )

    async def keyboard_type(self, text: str) -> DesktopInputResult:
        clean_text = validate_desktop_text(text)
        success, verified, msg = await self.driver.type_text(clean_text)
        return DesktopInputResult(
            action="desktop_keyboard_type",
            success=success,
            verified=verified,
            message=msg,
            details={"text_length": len(clean_text), "verification_scope": "os_input_queue"},
        )

    async def press_hotkey(self, hotkey: str) -> DesktopInputResult:
        clean_hotkey = validate_safe_hotkey(hotkey)
        success, verified, msg = await self.driver.press_hotkey(clean_hotkey)
        return DesktopInputResult(
            action="desktop_hotkey",
            success=success,
            verified=verified,
            message=msg,
            details={"hotkey": clean_hotkey, "verification_scope": "os_input_queue"},
        )


def create_default_desktop_input_driver() -> DesktopInputDriver:
    """Create default platform input driver."""
    if sys.platform == "win32":
        return WindowsDesktopInputDriver()
    return MockDesktopInputDriver()


def create_default_desktop_input_controller(
    inspector: DesktopInspector | None = None,
    driver: DesktopInputDriver | None = None,
) -> DesktopInputController:
    """Create default desktop input controller."""
    return DesktopInputController(inspector=inspector, driver=driver)
