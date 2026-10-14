"""A deterministic, read-only application awareness tool."""
from __future__ import annotations

import ctypes
from typing import Any

import psutil
from pydantic import BaseModel, Field

from app.security.permissions import PermissionLevel
from app.tools.base import Tool


class ApplicationListInput(BaseModel):
    limit: int = Field(default=50, ge=1, le=100, description="Maximum number of applications to list (1-100)")


class ApplicationListTool(Tool[ApplicationListInput]):
    """Return a read-only list of running applications without exposing private data or window contents."""

    name = "application_list"
    description = "Inspect currently running user applications and window titles safely."
    input_model = ApplicationListInput
    permission_level = PermissionLevel.SAFE

    @staticmethod
    def _get_visible_window_titles() -> list[dict[str, Any]]:
        """Safely enumerate top-level visible window titles via Windows User32 if accessible."""
        titles: list[dict[str, Any]] = []
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_windows_callback(hwnd: int, _: int) -> bool:
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if 0 < length < 256:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        text = buff.value.strip()
                        # Exclude blank or generic system window titles
                        if text and text not in ("Program Manager", "Default IME", "MSCTFIME UI"):
                            # Data minimization: truncate long titles to avoid leaking sensitive contents
                            safe_title = text[:80]
                            titles.append({"title": safe_title, "type": "window"})
                return True

            cb = WNDENUMPROC(enum_windows_callback)
            user32.EnumWindows(cb, 0)
        except Exception:
            pass
        return titles

    async def execute(self, input_data: ApplicationListInput) -> dict[str, object]:
        seen_names: set[str] = set()
        applications: list[dict[str, Any]] = []

        # 1. Inspect visible desktop windows if available
        window_apps = self._get_visible_window_titles()
        for item in window_apps:
            title = item["title"]
            if title.lower() not in seen_names:
                seen_names.add(title.lower())
                applications.append({"name": title, "kind": "window"})
                if len(applications) >= input_data.limit:
                    break

        # 2. Inspect active user processes (excluding low-level kernel/system daemons)
        system_daemons = {
            "system", "system idle process", "registry", "smss.exe", "csrss.exe",
            "wininit.exe", "services.exe", "lsass.exe", "svchost.exe", "fontdrvhost.exe",
            "dwm.exe", "memory compression", "winlogon.exe"
        }

        if len(applications) < input_data.limit:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    p_name = proc.info.get("name")
                    if not p_name or p_name.lower() in system_daemons:
                        continue
                    clean_name = p_name.strip()
                    if clean_name.lower() not in seen_names:
                        seen_names.add(clean_name.lower())
                        applications.append({"name": clean_name, "kind": "process"})
                        if len(applications) >= input_data.limit:
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    continue

        return {
            "count": len(applications),
            "applications": applications,
        }
