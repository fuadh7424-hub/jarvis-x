"""Read-only browser inspector supporting CDP endpoints and safe desktop window fallback."""
from __future__ import annotations

import ctypes
import os
import urllib.parse
from typing import Any

import httpx
import psutil

from app.browser.models import BrowserStatus, BrowserTab, PageMetadata


SUPPORTED_BROWSERS: dict[str, str] = {
    "chrome.exe": "chrome",
    "msedge.exe": "msedge",
    "firefox.exe": "firefox",
    "brave.exe": "brave",
}

WINDOW_SUFFIXES: list[tuple[str, str]] = [
    (" - Google Chrome", "chrome"),
    (" - Microsoft Edge", "msedge"),
    (" - Mozilla Firefox", "firefox"),
    (" - Brave", "brave"),
]

SENSITIVE_QUERY_KEYS: set[str] = {
    "token", "access_token", "auth", "authorization", "secret",
    "password", "key", "api_key", "session", "session_id", "jwt",
    "bearer", "code", "client_secret",
}


class BrowserInspector:
    """Safe, read-only inspector for browser status, tabs, and page metadata."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    @staticmethod
    def sanitize_url(raw_url: str | None) -> str | None:
        """Strip embedded credentials and redact sensitive tokens from query parameters."""
        if not raw_url:
            return None
        try:
            parts = urllib.parse.urlsplit(raw_url.strip())
            netloc = parts.netloc
            # Strip embedded user:password credentials
            if "@" in netloc:
                netloc = netloc.split("@")[-1]

            # Redact sensitive query parameters
            if parts.query:
                query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                sanitized_pairs: list[tuple[str, str]] = []
                for k, v in query_pairs:
                    if k.lower() in SENSITIVE_QUERY_KEYS or any(s in k.lower() for s in ("token", "secret", "password", "key")):
                        sanitized_pairs.append((k, "[REDACTED]"))
                    else:
                        sanitized_pairs.append((k, v))
                query = urllib.parse.urlencode(sanitized_pairs)
            else:
                query = parts.query

            sanitized = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
            return sanitized
        except Exception:
            return None

    @staticmethod
    def sanitize_title(raw_title: str | None) -> str:
        """Sanitize and bound page title."""
        if not raw_title or not raw_title.strip():
            return "Untitled"
        clean = raw_title.strip()
        return clean[:120]

    @staticmethod
    def detect_running_browsers() -> list[str]:
        """Detect active browser processes safely without altering system state."""
        detected: set[str] = set()
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name in SUPPORTED_BROWSERS:
                    detected.add(SUPPORTED_BROWSERS[name])
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
        return sorted(detected)

    @staticmethod
    def inspect_visible_browser_windows() -> list[BrowserTab]:
        """Enumerate visible desktop browser windows safely via Windows User32 if available."""
        tabs: list[BrowserTab] = []
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
                        for suffix, browser_name in WINDOW_SUFFIXES:
                            if text.endswith(suffix):
                                title = text[:-len(suffix)].strip()
                                clean_title = BrowserInspector.sanitize_title(title)
                                tab_id = f"win-{browser_name}-{len(tabs) + 1}"
                                tabs.append(
                                    BrowserTab(
                                        id=tab_id,
                                        title=clean_title,
                                        url=None,
                                        is_active=(len(tabs) == 0),
                                        browser=browser_name,
                                    )
                                )
                                break
                return True

            cb = WNDENUMPROC(enum_windows_callback)
            user32.EnumWindows(cb, 0)
        except Exception:
            pass
        return tabs

    async def _fetch_cdp(self, port: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Query local Chromium DevTools HTTP endpoints safely with a strict short timeout."""
        base_url = f"http://127.0.0.1:{port}"
        try:
            if self._client:
                version_resp = await self._client.get(f"{base_url}/json/version")
                list_resp = await self._client.get(f"{base_url}/json/list")
            else:
                async with httpx.AsyncClient(timeout=0.25) as client:
                    version_resp = await client.get(f"{base_url}/json/version")
                    list_resp = await client.get(f"{base_url}/json/list")

            if version_resp.status_code == 200 and list_resp.status_code == 200:
                version_data = version_resp.json()
                list_data = list_resp.json()
                if isinstance(list_data, list):
                    return version_data, list_data
        except (httpx.HTTPError, ValueError, TypeError, OSError):
            pass
        return None, []

    async def get_status(self, port: int = 9222) -> dict[str, object]:
        """Report browser integration availability and connection status."""
        detected = self.detect_running_browsers()
        is_running = len(detected) > 0
        version_data, _ = await self._fetch_cdp(port)
        connected = version_data is not None

        browser_name = None
        browser_version = None
        endpoint = None

        if connected and version_data:
            browser_name = version_data.get("Browser")
            browser_version = version_data.get("Protocol-Version")
            endpoint = f"http://127.0.0.1:{port}"
            message = "Browser connected via local inspection endpoint."
        elif is_running:
            message = (
                f"Browsers running ({', '.join(detected)}). "
                f"Local inspection port {port} not enabled (start browser with --remote-debugging-port={port} for deep tab inspection)."
            )
        else:
            message = "No supported browsers currently running."

        status = BrowserStatus(
            supported_browsers=list(SUPPORTED_BROWSERS.values()),
            detected_browsers=detected,
            is_running=is_running,
            connected=connected,
            browser_name=browser_name,
            browser_version=browser_version,
            inspection_endpoint=endpoint,
            message=message,
        )
        return status.model_dump()

    async def get_tabs(self, limit: int = 50, port: int = 9222) -> dict[str, object]:
        """Return safe metadata for open browser tabs without exposing sensitive data."""
        # 1. Try local CDP endpoint first
        version_data, list_data = await self._fetch_cdp(port)
        if version_data is not None and list_data:
            cdp_tabs: list[BrowserTab] = []
            browser_brand = (version_data.get("Browser") or "chromium").split("/")[0].lower()
            for idx, item in enumerate(list_data):
                if not isinstance(item, dict):
                    continue
                if item.get("type") not in ("page", "app", None):
                    continue
                tab_id = str(item.get("id") or f"tab-{idx}")
                title = self.sanitize_title(item.get("title"))
                url = self.sanitize_url(item.get("url"))
                cdp_tabs.append(
                    BrowserTab(
                        id=tab_id,
                        title=title,
                        url=url,
                        is_active=(len(cdp_tabs) == 0),
                        browser=browser_brand,
                    )
                )

            bounded = cdp_tabs[:limit]
            return {
                "total_count": len(cdp_tabs),
                "returned_count": len(bounded),
                "source": "cdp",
                "tabs": [t.model_dump() for t in bounded],
            }

        # 2. Fall back to visible desktop browser windows
        win_tabs = self.inspect_visible_browser_windows()
        if win_tabs:
            bounded_win = win_tabs[:limit]
            return {
                "total_count": len(win_tabs),
                "returned_count": len(bounded_win),
                "source": "window_inspection",
                "tabs": [t.model_dump() for t in bounded_win],
            }

        # 3. No tabs found
        return {
            "total_count": 0,
            "returned_count": 0,
            "source": "none",
            "tabs": [],
        }

    async def get_active_tab(self, port: int = 9222) -> dict[str, object]:
        """Return metadata for the currently active tab (URL and title only)."""
        tabs_res = await self.get_tabs(limit=1, port=port)
        tabs = tabs_res.get("tabs")
        source = tabs_res.get("source", "none")

        if isinstance(tabs, list) and len(tabs) > 0:
            first_tab = tabs[0]
            return {
                "found": True,
                "id": first_tab.get("id"),
                "title": first_tab.get("title"),
                "url": first_tab.get("url"),
                "browser": first_tab.get("browser"),
                "source": source,
            }

        return {
            "found": False,
            "id": None,
            "title": None,
            "url": None,
            "browser": None,
            "source": "none",
        }

    async def get_page_metadata(self, port: int = 9222) -> dict[str, object]:
        """Return safe high-level metadata about the active page."""
        active = await self.get_active_tab(port=port)
        if not active.get("found"):
            return PageMetadata(found=False, source="none").model_dump()

        url_str = active.get("url")
        title_str = active.get("title")
        domain = None
        protocol = None
        is_secure = None

        if url_str:
            try:
                parsed = urllib.parse.urlsplit(url_str)
                domain = parsed.hostname
                protocol = parsed.scheme
                is_secure = (protocol == "https")
            except Exception:
                pass

        meta = PageMetadata(
            found=True,
            title=title_str,
            url=url_str,
            domain=domain,
            protocol=protocol,
            is_secure=is_secure,
            source=active.get("source") or "none",
        )
        return meta.model_dump()
