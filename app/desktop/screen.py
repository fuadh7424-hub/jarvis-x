"""Controlled, secure desktop screen awareness and capture subsystem."""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, Field

from app.desktop.inspector import DesktopInspector
from app.desktop.models import DisplayBounds, DisplayInfo


class ScreenCaptureError(Exception):
    """Base exception for screen capture failures."""


class InvalidDisplayError(ScreenCaptureError):
    """Raised when an invalid or nonexistent display ID is requested."""


class ScreenCaptureResult(BaseModel):
    """Structured result of a controlled desktop screen capture."""
    capture_id: str = Field(description="Unique capture identifier")
    display_id: str = Field(description="Display identifier that was captured (or 'all')")
    width: int = Field(description="Captured image width in pixels")
    height: int = Field(description="Captured image height in pixels")
    format: str = Field(default="PNG", description="Image encoding format")
    size_bytes: int = Field(description="Byte size of the encoded image")
    timestamp_utc: str = Field(description="ISO-8601 UTC timestamp of the capture")
    temp_file_path: str | None = Field(default=None, description="Path to temporary file if saved")
    base64_data: str | None = Field(default=None, description="Base64 encoded image if requested")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Safe capture metadata")


@runtime_checkable
class ScreenCaptureProvider(Protocol):
    """Interface for capturing screen pixels."""

    def capture_screen(
        self,
        bbox: tuple[int, int, int, int] | None = None,
        all_displays: bool = False,
    ) -> Any:
        """Capture screen pixels into a PIL Image."""
        ...


class WindowsScreenCaptureProvider:
    """Windows native screen capture provider using PIL.ImageGrab."""

    def capture_screen(
        self,
        bbox: tuple[int, int, int, int] | None = None,
        all_displays: bool = False,
    ) -> Any:
        try:
            from PIL import ImageGrab
            return ImageGrab.grab(bbox=bbox, all_screens=all_displays)
        except Exception as exc:
            raise ScreenCaptureError(f"Native screen capture failed: {exc}") from exc


class MockScreenCaptureProvider:
    """Deterministic in-memory mock screen capture provider for unit testing."""

    def __init__(
        self,
        default_size: tuple[int, int] = (1920, 1080),
        color: tuple[int, int, int] = (40, 44, 52),
    ) -> None:
        self.default_size = default_size
        self.color = color
        self.capture_count = 0
        self.last_bbox: tuple[int, int, int, int] | None = None
        self.last_all_displays: bool = False

    def capture_screen(
        self,
        bbox: tuple[int, int, int, int] | None = None,
        all_displays: bool = False,
    ) -> Any:
        from PIL import Image

        self.capture_count += 1
        self.last_bbox = bbox
        self.last_all_displays = all_displays

        if bbox is not None:
            w = max(1, bbox[2] - bbox[0])
            h = max(1, bbox[3] - bbox[1])
            size = (w, h)
        elif all_displays:
            size = (self.default_size[0] * 2, self.default_size[1])
        else:
            size = self.default_size

        return Image.new("RGB", size, self.color)


# Future Visual Understanding / OCR Abstractions
class OCRTextElement(BaseModel):
    """Detected text element with position and confidence."""
    text: str = Field(description="Sanitized text content")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    bounds: tuple[int, int, int, int] | None = Field(default=None, description="(left, top, right, bottom)")


class OCRResult(BaseModel):
    """Structured OCR extraction result."""
    elements: list[OCRTextElement] = Field(default_factory=list)
    full_text: str = Field(default="")
    element_count: int = Field(default=0)


@runtime_checkable
class OCRProvider(Protocol):
    """Typed abstraction for local OCR capabilities."""

    def extract_text(self, image: Any) -> OCRResult:
        """Extract text from an image safely."""
        ...


class MockOCRProvider:
    """Mock OCR provider enforcing strict sanitization of extracted content."""

    def __init__(self, canned_elements: list[OCRTextElement] | None = None) -> None:
        self._canned = canned_elements or []

    def extract_text(self, image: Any) -> OCRResult:
        full = "\n".join(e.text for e in self._canned)
        return OCRResult(
            elements=self._canned,
            full_text=full,
            element_count=len(self._canned),
        )


class VisionAnalysisResult(BaseModel):
    """Safe structured result of high-level visual analysis."""
    description: str = Field(default="", description="Safe textual description of visual scene")
    layout_elements: list[dict[str, Any]] = Field(default_factory=list, description="Safe layout primitives")


@runtime_checkable
class VisionProvider(Protocol):
    """Typed abstraction for future visual scene understanding (no external network call)."""

    def analyze(self, image: Any, prompt: str = "") -> VisionAnalysisResult:
        """Analyze visual layout and structure."""
        ...


class MockVisionProvider:
    """Mock vision provider for unit tests without external network or AI model calls."""

    def __init__(self, description: str = "Mock visual scene") -> None:
        self.description = description
        self.call_count = 0

    def analyze(self, image: Any, prompt: str = "") -> VisionAnalysisResult:
        self.call_count += 1
        return VisionAnalysisResult(
            description=self.description,
            layout_elements=[{"type": "window", "label": "Mock Window"}],
        )


class DesktopScreenController:
    """Controller orchestrating controlled desktop screen capture and lifecycle."""

    def __init__(
        self,
        inspector: DesktopInspector,
        provider: ScreenCaptureProvider | None = None,
        temp_dir: Path | None = None,
    ) -> None:
        self.inspector = inspector
        self.provider = provider or self._default_provider()
        self.temp_dir = temp_dir or (Path(tempfile.gettempdir()) / "jarvis_x_screen_temp")
        self._created_temp_files: dict[str, Path] = {}

    @staticmethod
    def _default_provider() -> ScreenCaptureProvider:
        if sys.platform == "win32":
            return WindowsScreenCaptureProvider()
        return MockScreenCaptureProvider()

    def _ensure_temp_dir(self) -> Path:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        return self.temp_dir

    async def capture(
        self,
        display_id: str | None = None,
        all_displays: bool = False,
        save_to_temp: bool = False,
        include_base64: bool = False,
    ) -> ScreenCaptureResult:
        """Safely capture screen metadata and pixels based on validated parameters."""
        displays_dict = await self.inspector.get_displays()
        displays: list[dict[str, Any]] = displays_dict.get("displays", [])

        bbox: tuple[int, int, int, int] | None = None
        effective_display_id = display_id

        if all_displays:
            effective_display_id = "all"
            bbox = None
        elif display_id is not None:
            # Validate display_id against available displays
            matching = [d for d in displays if d.get("id") == display_id]
            if not matching:
                available = [d.get("id", "unknown") for d in displays]
                raise InvalidDisplayError(
                    f"Requested display '{display_id}' not found. Available displays: {available}"
                )
            bounds = matching[0].get("bounds", {})
            bbox = (
                bounds.get("left", 0),
                bounds.get("top", 0),
                bounds.get("right", 0),
                bounds.get("bottom", 0),
            )
        else:
            # Default to primary display
            primary_idx = displays_dict.get("primary_display_index", 0)
            if displays and 0 <= primary_idx < len(displays):
                primary = displays[primary_idx]
                effective_display_id = primary.get("id", "display-1")
                bounds = primary.get("bounds", {})
                bbox = (
                    bounds.get("left", 0),
                    bounds.get("top", 0),
                    bounds.get("right", 0),
                    bounds.get("bottom", 0),
                )
            else:
                effective_display_id = "primary"
                bbox = None

        # Execute capture via provider
        image = self.provider.capture_screen(bbox=bbox, all_displays=all_displays)

        # Encode to PNG bytes in memory
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        capture_id = str(uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()
        temp_file_path: str | None = None

        if save_to_temp:
            td = self._ensure_temp_dir()
            filename = f"capture_{capture_id}.png"
            target_path = td / filename
            target_path.write_bytes(png_bytes)
            self._created_temp_files[capture_id] = target_path
            temp_file_path = str(target_path)

        base64_data: str | None = None
        if include_base64:
            base64_data = base64.b64encode(png_bytes).decode("ascii")

        return ScreenCaptureResult(
            capture_id=capture_id,
            display_id=effective_display_id or "primary",
            width=image.width,
            height=image.height,
            format="PNG",
            size_bytes=len(png_bytes),
            timestamp_utc=now_utc,
            temp_file_path=temp_file_path,
            base64_data=base64_data,
            metadata={
                "all_displays": all_displays,
                "bbox": bbox,
                "saved_to_temp": save_to_temp,
            },
        )

    def cleanup_capture(self, capture_id: str) -> bool:
        """Remove a temporary screenshot file by its capture ID."""
        path = self._created_temp_files.pop(capture_id, None)
        if path and path.exists():
            try:
                path.unlink()
                return True
            except OSError:
                return False
        return False

    def cleanup_all(self) -> int:
        """Remove all temporary screenshot files managed by this controller."""
        removed = 0
        for path in list(self._created_temp_files.values()):
            if path.exists():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        self._created_temp_files.clear()
        return removed


def create_default_desktop_screen_controller(
    inspector: DesktopInspector | None = None,
    provider: ScreenCaptureProvider | None = None,
    temp_dir: Path | None = None,
) -> DesktopScreenController:
    """Factory creating a configured desktop screen controller."""
    insp = inspector or DesktopInspector()
    return DesktopScreenController(inspector=insp, provider=provider, temp_dir=temp_dir)
