"""Read-only desktop inspector for windows, active window, and displays."""
from __future__ import annotations

import os
import re
import sys
from typing import Any, Protocol

import psutil

from app.desktop.models import (
    ActiveWindow,
    DesktopWindow,
    DisplayBounds,
    DisplayInfo,
    WindowBounds,
)


def sanitize_window_title(raw_title: str | None) -> str:
    """Sanitize window title to prevent exposure of sensitive paths, credentials, and tokens."""
    if not raw_title or not raw_title.strip():
        return "Untitled"
    clean = raw_title.strip()

    # 1. Redact Windows user profile paths: C:\Users\<username>\ or C:/Users/<username>/
    clean = re.sub(r"(?i)([a-z]:[/\\]users[/\\])[^/\\ ]+([/\\])", r"\1[REDACTED]\2", clean)

    # 2. Redact Unix/POSIX user profile paths: /home/<username>/ or /Users/<username>/
    clean = re.sub(r"(?i)(/(?:home|users)/)[^/ ]+(/)", r"\1[REDACTED]\2", clean)

    # 3. Redact sensitive files and extensions
    sensitive_file_patterns = [
        r"(?i)\.env(?:\.[a-z0-9_-]+)?",
        r"(?i)\bid_rsa(?:\.pub)?\b",
        r"(?i)\bid_ed25519(?:\.pub)?\b",
        r"(?i)\bid_ecdsa(?:\.pub)?\b",
        r"(?i)\bcredentials(?:\.json|\.yaml|\.yml|\.xml)\b",
        r"(?i)\bsecrets?(?:\.json|\.yaml|\.yml)\b",
        r"(?i)\b[a-z0-9_\-\.]+\.(?:pem|key|pfx|p12|pkcs12)\b",
    ]
    for pattern in sensitive_file_patterns:
        clean = re.sub(pattern, "[REDACTED_FILE]", clean)

    # 4. Redact JWT tokens
    clean = re.sub(r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "[REDACTED_JWT]", clean)

    # 5. Redact Bearer authorization headers/strings
    clean = re.sub(r"(?i)\bbearer\s+[a-z0-9_\-\.]{15,}\b", "Bearer [REDACTED]", clean)

    # 6. Redact high-entropy API key patterns
    clean = re.sub(r"\b(?:sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z-_]{35})\b", "[REDACTED_KEY]", clean)

    # 7. Redact credit card patterns (16 digits in groups of 4)
    clean = re.sub(r"\b(?:\d{4}[ -]?){3}\d{4}\b", "[REDACTED_CARD]", clean)

    # 8. Redact key-value credentials, tokens, passwords, session ids
    cred_kv_pattern = (
        r"(?i)\b(?:token|auth|authorization|api_key|secret|password|passwd|pwd|session|session_id|private_key)"
        r"\s*[:=]\s*(?!Bearer\s+\[REDACTED\])(?!\[REDACTED)[^\s,;]+"
    )
    clean = re.sub(cred_kv_pattern, "[REDACTED]", clean)

    # 9. Bounded length (max 120 chars)
    clean = clean[:120].strip()
    return clean or "Untitled"


class DesktopDriver(Protocol):
    """Protocol for reading desktop window and display state."""

    def get_windows(self, limit: int = 50) -> list[DesktopWindow]: ...
    def get_active_window(self) -> ActiveWindow: ...
    def get_displays(self) -> list[DisplayInfo]: ...


class WindowsDesktopDriver:
    """Read-only driver using Windows User32 and psutil."""

    def get_windows(self, limit: int = 50) -> list[DesktopWindow]:
        windows: list[DesktopWindow] = []
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_cb(hwnd: int, _: int) -> bool:
                if len(windows) >= limit:
                    return False
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        raw_title = buff.value.strip()
                        if raw_title:
                            rect = wintypes.RECT()
                            user32.GetWindowRect(hwnd, ctypes.byref(rect))
                            w = rect.right - rect.left
                            h = rect.bottom - rect.top
                            # Filter out zero-size ghost or tooltip windows
                            if w > 0 and h > 0:
                                pid = wintypes.DWORD()
                                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                                pname = None
                                if pid.value:
                                    try:
                                        pname = psutil.Process(pid.value).name()
                                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                                        pname = None
                                is_minimized = bool(user32.IsIconic(hwnd))
                                clean_title = sanitize_window_title(raw_title)
                                window_id = f"win-{hex(hwnd)}"
                                bounds = WindowBounds(left=rect.left, top=rect.top, width=w, height=h)
                                windows.append(
                                    DesktopWindow(
                                        window_id=window_id,
                                        title=clean_title,
                                        process_id=pid.value if pid.value else None,
                                        process_name=pname,
                                        is_minimized=is_minimized,
                                        bounds=bounds,
                                    )
                                )
                return True

            cb = WNDENUMPROC(enum_cb)
            user32.EnumWindows(cb, 0)
        except Exception:
            pass
        return windows

    def get_active_window(self) -> ActiveWindow:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd and user32.IsWindow(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                raw_title = ""
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    raw_title = buff.value.strip()

                clean_title = sanitize_window_title(raw_title) if raw_title else "Untitled"
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pname = None
                if pid.value:
                    try:
                        pname = psutil.Process(pid.value).name()
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                        pname = None

                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                bounds = WindowBounds(
                    left=rect.left,
                    top=rect.top,
                    width=rect.right - rect.left,
                    height=rect.bottom - rect.top,
                )
                is_minimized = bool(user32.IsIconic(hwnd))
                return ActiveWindow(
                    found=True,
                    window_id=f"win-{hex(hwnd)}",
                    title=clean_title,
                    process_id=pid.value if pid.value else None,
                    process_name=pname,
                    is_minimized=is_minimized,
                    bounds=bounds,
                )
        except Exception:
            pass
        return ActiveWindow(found=False)

    def get_displays(self) -> list[DisplayInfo]:
        displays: list[DisplayInfo] = []
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            class MONITORINFOEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32),
                ]

            def monitor_cb(hMonitor: int, hdc: int, lprc: Any, lparam: int) -> bool:
                info = MONITORINFOEXW()
                info.cbSize = ctypes.sizeof(MONITORINFOEXW)
                if user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
                    is_primary = bool(info.dwFlags & 1)
                    disp_id = f"display-{len(displays) + 1}"
                    bounds = DisplayBounds(
                        left=info.rcMonitor.left,
                        top=info.rcMonitor.top,
                        right=info.rcMonitor.right,
                        bottom=info.rcMonitor.bottom,
                        width=info.rcMonitor.right - info.rcMonitor.left,
                        height=info.rcMonitor.bottom - info.rcMonitor.top,
                    )
                    work_area = DisplayBounds(
                        left=info.rcWork.left,
                        top=info.rcWork.top,
                        right=info.rcWork.right,
                        bottom=info.rcWork.bottom,
                        width=info.rcWork.right - info.rcWork.left,
                        height=info.rcWork.bottom - info.rcWork.top,
                    )
                    displays.append(
                        DisplayInfo(
                            id=disp_id,
                            is_primary=is_primary,
                            bounds=bounds,
                            work_area=work_area,
                            scale_factor=1.0,
                        )
                    )
                return True

            MONITORENUMPROC = ctypes.WINFUNCTYPE(
                wintypes.BOOL,
                wintypes.HMONITOR,
                wintypes.HDC,
                ctypes.POINTER(wintypes.RECT),
                wintypes.LPARAM,
            )
            cb = MONITORENUMPROC(monitor_cb)
            user32.EnumDisplayMonitors(None, None, cb, 0)
        except Exception:
            pass

        # Fallback if EnumDisplayMonitors returned nothing (e.g. headless or service session)
        if not displays:
            try:
                import ctypes

                user32 = ctypes.windll.user32
                w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
                h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
                if w > 0 and h > 0:
                    bounds = DisplayBounds(left=0, top=0, right=w, bottom=h, width=w, height=h)
                    displays.append(
                        DisplayInfo(
                            id="display-1",
                            is_primary=True,
                            bounds=bounds,
                            work_area=bounds,
                            scale_factor=1.0,
                        )
                    )
            except Exception:
                pass
        return displays


class MockDesktopDriver:
    """Mock desktop driver for deterministic unit tests."""

    def __init__(
        self,
        windows: list[DesktopWindow] | None = None,
        active_window: ActiveWindow | None = None,
        displays: list[DisplayInfo] | None = None,
    ) -> None:
        self._windows = list(windows) if windows is not None else []
        self._active_window = active_window or ActiveWindow(found=False)
        self._displays = list(displays) if displays is not None else []

    def get_windows(self, limit: int = 50) -> list[DesktopWindow]:
        return self._windows[:limit]

    def get_active_window(self) -> ActiveWindow:
        return self._active_window

    def get_displays(self) -> list[DisplayInfo]:
        return self._displays


class DesktopInspector:
    """Safe, read-only inspector for desktop windows, active focus, and displays."""

    def __init__(self, driver: DesktopDriver | None = None) -> None:
        self._driver = driver or self._default_driver()

    @staticmethod
    def _default_driver() -> DesktopDriver:
        if sys.platform == "win32":
            return WindowsDesktopDriver()
        return MockDesktopDriver()

    @staticmethod
    def sanitize_title(raw_title: str | None) -> str:
        return sanitize_window_title(raw_title)

    async def get_windows(self, limit: int = 50) -> dict[str, object]:
        """Enumerate visible desktop application windows safely."""
        windows = self._driver.get_windows(limit=limit)
        return {
            "window_count": len(windows),
            "windows": [w.model_dump() for w in windows],
        }

    async def get_active_window(self) -> dict[str, object]:
        """Inspect the currently focused top-level window safely."""
        active = self._driver.get_active_window()
        return active.model_dump()

    async def get_displays(self) -> dict[str, object]:
        """Inspect connected displays and resolutions safely without capturing screen pixels."""
        displays = self._driver.get_displays()
        primary_idx = 0
        for i, d in enumerate(displays):
            if d.is_primary:
                primary_idx = i
                break
        return {
            "display_count": len(displays),
            "primary_display_index": primary_idx,
            "displays": [d.model_dump() for d in displays],
        }
