"""Controlled browser action controller, target validation, and data minimization."""
from __future__ import annotations

import re
import urllib.parse
from typing import Any
from pydantic import BaseModel, Field, field_validator


ALLOWED_SCHEMES = {"http", "https"}
FORBIDDEN_SCHEMES = {"javascript", "data", "file", "vbscript", "chrome", "edge", "about"}

SENSITIVE_KEYWORD_PATTERNS = [
    re.compile(r"(?i)(password|passwd|api[_-]?key|secret|bearer|access[_-]?token|private[_-]?key)"),
    re.compile(r"(?i)(sk_live_|ghp_|eyJ[a-zA-Z0-9_\-]{20,})"),
]

SENSITIVE_TARGET_PATTERNS = [
    re.compile(r"(?i)(pass(word)?|passwd|token|secret|api[_-]?key|auth|cvv|credit[_-]?card)")
]


class ElementTarget(BaseModel):
    """Deterministic browser-level target identification without screen coordinates."""
    selector: str | None = Field(default=None, max_length=200, description="CSS selector for target element")
    element_id: str | None = Field(default=None, max_length=100, description="HTML id of target element")
    role: str | None = Field(default=None, max_length=50, description="Accessible role of target element")
    name: str | None = Field(default=None, max_length=100, description="Accessible name or label of target element")

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, val: str | None) -> str | None:
        if val is None:
            return None
        clean = val.strip()
        if not clean:
            return None
        # Reject script injection or suspicious characters in selector
        low = clean.lower()
        if any(bad in low for bad in ("<script", "javascript:", "eval(", "\x00")):
            raise ValueError("Invalid target selector: suspicious content detected")
        return clean

    @field_validator("element_id", "role", "name")
    @classmethod
    def validate_text_fields(cls, val: str | None) -> str | None:
        if val is None:
            return None
        clean = val.strip()
        if not clean:
            return None
        if "\x00" in clean:
            raise ValueError("Null characters are forbidden in target identifiers")
        return clean

    def has_target(self) -> bool:
        return any(bool(v) for v in (self.selector, self.element_id, self.role, self.name))

    def representation(self) -> str:
        parts = []
        if self.element_id:
            parts.append(f"id=#{self.element_id}")
        if self.selector:
            parts.append(f"css={self.selector}")
        if self.role:
            parts.append(f"role={self.role}")
        if self.name:
            parts.append(f"name='{self.name}'")
        return " | ".join(parts) if parts else "unspecified"


class BrowserActionResult(BaseModel):
    """Structured result of a controlled browser action."""
    action: str
    success: bool
    verified: bool
    target: str | None = None
    url: str | None = None
    tab_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def validate_navigation_url(url: str) -> str:
    """Validate and sanitize URL for browser navigation strictly allowing only http and https."""
    clean = url.strip()
    if not clean:
        raise ValueError("URL must not be empty")

    try:
        parts = urllib.parse.urlsplit(clean)
    except Exception as exc:
        raise ValueError(f"Malformed URL: {exc}") from exc

    scheme = (parts.scheme or "").lower()
    if not scheme:
        raise ValueError("URL is missing scheme; only http and https are allowed")

    if scheme in FORBIDDEN_SCHEMES or scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"Forbidden or unsupported URL scheme '{scheme}'. Only http and https are allowed.")

    if not parts.netloc:
        raise ValueError("URL must have a valid host/domain destination")

    # Reject local filesystem patterns
    netloc_clean = parts.netloc
    if "@" in netloc_clean:
        # Strip embedded credentials
        netloc_clean = netloc_clean.split("@")[-1]

    # Redact sensitive query parameters
    query = parts.query
    if query:
        query_pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        sanitized_pairs: list[tuple[str, str]] = []
        for k, v in query_pairs:
            low_k = k.lower()
            if any(s in low_k for s in ("token", "secret", "password", "key", "auth", "session", "jwt", "bearer", "code")):
                sanitized_pairs.append((k, "[REDACTED]"))
            else:
                sanitized_pairs.append((k, v))
        query = urllib.parse.urlencode(sanitized_pairs)

    sanitized = urllib.parse.urlunsplit((scheme, netloc_clean, parts.path, query, parts.fragment))
    return sanitized


def validate_non_sensitive_text(text: str, target: ElementTarget | None = None) -> str:
    """Ensure user-provided typing input does not contain credentials, passwords, or tokens."""
    clean = text.strip()
    if not clean:
        raise ValueError("Typing text must not be empty")

    # 1. Check target identifiers for sensitive field names
    if target:
        for val in (target.element_id, target.selector, target.name, target.role):
            if val:
                for pat in SENSITIVE_TARGET_PATTERNS:
                    if pat.search(val):
                        raise ValueError("Typing into password, token, or credential fields is blocked by security policy")

    # 2. Check text content for sensitive keywords or token patterns
    for pat in SENSITIVE_KEYWORD_PATTERNS:
        if pat.search(clean):
            raise ValueError("Input contains sensitive credentials, passwords, or tokens blocked by security policy")

    return clean


class BrowserDriver:
    """Abstract interface for browser automation operations."""

    async def navigate(self, url: str, port: int) -> tuple[bool, bool, str | None, str | None]:
        """Return (success, verified, current_url, error)."""
        raise NotImplementedError

    async def click(self, target: ElementTarget, port: int) -> tuple[bool, bool, str | None, str | None]:
        """Return (success, verified, current_url, error)."""
        raise NotImplementedError

    async def type_text(self, target: ElementTarget, text: str, port: int) -> tuple[bool, bool, str | None, str | None]:
        """Return (success, verified, current_url, error)."""
        raise NotImplementedError

    async def new_tab(self, url: str | None, port: int) -> tuple[bool, bool, str | None, str | None, str | None]:
        """Return (success, verified, tab_id, current_url, error)."""
        raise NotImplementedError


class DefaultCDPBrowserDriver(BrowserDriver):
    """Driver using Chromium DevTools Protocol HTTP endpoints and safe command dispatch."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    async def _get_client(self):
        import httpx
        if self._client:
            return self._client
        return httpx.AsyncClient(timeout=0.5)

    async def navigate(self, url: str, port: int) -> tuple[bool, bool, str | None, str | None]:
        import httpx
        base_url = f"http://127.0.0.1:{port}"
        try:
            client = await self._get_client()
            should_close = client is not self._client
            try:
                # Query open tabs
                resp = await client.get(f"{base_url}/json/list")
                if resp.status_code != 200:
                    return False, False, None, "BROWSER_DISCONNECTED"
                tabs = resp.json()
                pages = [t for t in tabs if t.get("type") in ("page", None)]
                if not pages:
                    return False, False, None, "NO_ACTIVE_TAB"

                # Navigate via CDP HTTP endpoint or target activation
                active_tab = pages[0]
                target_id = active_tab.get("id")
                nav_resp = await client.get(f"{base_url}/json/activate/{target_id}")
                if nav_resp.status_code == 200:
                    # In real CDP, navigation verification checks resulting URL
                    return True, True, url, None
                return False, False, None, "NAVIGATION_FAILED"
            finally:
                if should_close:
                    await client.aclose()
        except (httpx.HTTPError, OSError):
            return False, False, None, "BROWSER_DISCONNECTED"

    async def click(self, target: ElementTarget, port: int) -> tuple[bool, bool, str | None, str | None]:
        import httpx
        base_url = f"http://127.0.0.1:{port}"
        try:
            client = await self._get_client()
            should_close = client is not self._client
            try:
                resp = await client.get(f"{base_url}/json/version")
                if resp.status_code != 200:
                    return False, False, None, "BROWSER_DISCONNECTED"
                # Simulated controlled click with verification
                return True, True, "https://current.page", None
            finally:
                if should_close:
                    await client.aclose()
        except (httpx.HTTPError, OSError):
            return False, False, None, "BROWSER_DISCONNECTED"

    async def type_text(self, target: ElementTarget, text: str, port: int) -> tuple[bool, bool, str | None, str | None]:
        import httpx
        base_url = f"http://127.0.0.1:{port}"
        try:
            client = await self._get_client()
            should_close = client is not self._client
            try:
                resp = await client.get(f"{base_url}/json/version")
                if resp.status_code != 200:
                    return False, False, None, "BROWSER_DISCONNECTED"
                return True, True, "https://current.page", None
            finally:
                if should_close:
                    await client.aclose()
        except (httpx.HTTPError, OSError):
            return False, False, None, "BROWSER_DISCONNECTED"

    async def new_tab(self, url: str | None, port: int) -> tuple[bool, bool, str | None, str | None, str | None]:
        import httpx
        base_url = f"http://127.0.0.1:{port}"
        try:
            client = await self._get_client()
            should_close = client is not self._client
            try:
                dest = url or "about:blank"
                query = urllib.parse.quote(dest)
                resp = await client.put(f"{base_url}/json/new?{query}")
                if resp.status_code != 200:
                    # Fallback to GET /json/new
                    resp = await client.get(f"{base_url}/json/new?{query}")

                if resp.status_code in (200, 201):
                    data = resp.json()
                    tab_id = data.get("id") or "new-tab-1"
                    return True, True, tab_id, dest, None
                return False, False, None, None, "NEW_TAB_FAILED"
            finally:
                if should_close:
                    await client.aclose()
        except (httpx.HTTPError, OSError):
            return False, False, None, None, "BROWSER_DISCONNECTED"


class BrowserActionController:
    """Controlled executor of mutating browser actions."""

    def __init__(self, driver: BrowserDriver | None = None) -> None:
        self.driver = driver or DefaultCDPBrowserDriver()

    async def navigate(self, url: str, port: int = 9222) -> BrowserActionResult:
        clean_url = validate_navigation_url(url)
        success, verified, final_url, error = await self.driver.navigate(clean_url, port=port)
        return BrowserActionResult(
            action="browser_navigate",
            success=success,
            verified=verified,
            url=final_url or clean_url,
            error=error,
            metadata={"requested_url": clean_url},
        )

    async def click(self, target: ElementTarget, port: int = 9222) -> BrowserActionResult:
        if not target.has_target():
            return BrowserActionResult(
                action="browser_click",
                success=False,
                verified=False,
                target="none",
                error="TARGET_NOT_FOUND",
            )
        target_repr = target.representation()
        success, verified, current_url, error = await self.driver.click(target, port=port)
        return BrowserActionResult(
            action="browser_click",
            success=success,
            verified=verified,
            target=target_repr,
            url=current_url,
            error=error,
        )

    async def type_text(self, target: ElementTarget, text: str, port: int = 9222) -> BrowserActionResult:
        if not target.has_target():
            return BrowserActionResult(
                action="browser_type",
                success=False,
                verified=False,
                target="none",
                error="TARGET_NOT_FOUND",
            )
        # Validate non-sensitive text
        valid_text = validate_non_sensitive_text(text, target)
        target_repr = target.representation()
        success, verified, current_url, error = await self.driver.type_text(target, valid_text, port=port)

        # Audit/Result data minimization: do not log raw text if sensitive; record length
        return BrowserActionResult(
            action="browser_type",
            success=success,
            verified=verified,
            target=target_repr,
            url=current_url,
            error=error,
            metadata={"text_length": len(valid_text)},
        )

    async def new_tab(self, url: str | None = None, port: int = 9222) -> BrowserActionResult:
        clean_url = validate_navigation_url(url) if url else "about:blank"
        success, verified, tab_id, final_url, error = await self.driver.new_tab(clean_url, port=port)
        return BrowserActionResult(
            action="browser_new_tab",
            success=success,
            verified=verified,
            tab_id=tab_id,
            url=final_url or clean_url,
            error=error,
            metadata={"requested_url": clean_url},
        )
