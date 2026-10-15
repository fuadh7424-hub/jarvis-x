"""Comprehensive test suite for Stage 10C: Secure Screen Awareness."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.agent import Agent
from app.agent.state import AgentStatus
from app.core.config import get_settings
from app.desktop.inspector import DesktopInspector, MockDesktopDriver
from app.desktop.models import DisplayBounds, DisplayInfo
from app.desktop.screen import (
    DesktopScreenController,
    InvalidDisplayError,
    MockOCRProvider,
    MockScreenCaptureProvider,
    MockVisionProvider,
    OCRProvider,
    OCRResult,
    OCRTextElement,
    ScreenCaptureError,
    ScreenCaptureProvider,
    VisionProvider,
    WindowsScreenCaptureProvider,
    create_default_desktop_screen_controller,
)
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.desktop_screen_capture import DesktopScreenCaptureInput, DesktopScreenCaptureTool
from app.tools.executor import (
    ApprovalConsumedError,
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalRejectedError,
    ApprovalRequiredError,
    Executor,
    InvalidToolInputError,
    ToolExecutionError,
)
from app.tools.registry import ToolRegistry
from main import create_app


def make_mock_displays() -> list[DisplayInfo]:
    return [
        DisplayInfo(
            id="display-1",
            is_primary=True,
            bounds=DisplayBounds(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080),
            work_area=DisplayBounds(left=0, top=0, right=1920, bottom=1040, width=1920, height=1040),
            scale_factor=1.0,
        ),
        DisplayInfo(
            id="display-2",
            is_primary=False,
            bounds=DisplayBounds(left=1920, top=0, right=3840, bottom=1080, width=1920, height=1080),
            work_area=DisplayBounds(left=1920, top=0, right=3840, bottom=1080, width=1920, height=1080),
            scale_factor=1.0,
        ),
    ]


def make_screen_executor(
    tmp_path: Path,
    displays: list[DisplayInfo] | None = None,
    mock_provider: MockScreenCaptureProvider | None = None,
) -> tuple[Executor, DesktopScreenController, MockScreenCaptureProvider]:
    display_list = displays or make_mock_displays()
    inspector_driver = MockDesktopDriver(windows=[], displays=display_list)
    inspector = DesktopInspector(driver=inspector_driver)
    provider = mock_provider or MockScreenCaptureProvider()
    temp_dir = tmp_path / "screen_temp"
    controller = DesktopScreenController(inspector=inspector, provider=provider, temp_dir=temp_dir)

    tool = DesktopScreenCaptureTool(inspector=inspector, controller=controller)
    registry = ToolRegistry()
    registry.register(tool)

    repo = SQLiteRepository(tmp_path / "jarvis.sqlite3")
    executor = Executor(registry, repo, audit_sink=lambda e: None)
    return executor, controller, provider


def approve_and_execute(executor: Executor, tool_name: str, payload: dict, requested_by: str = "owner") -> dict:
    approval = executor.create_approval(tool_name, payload, reason="Authorized screen capture", requested_by=requested_by)
    executor.resolve_approval(approval.id, approved=True)
    return asyncio.run(executor.execute(tool_name, payload, approval_id=approval.id))


# 1. Screen capture tool registration in ToolRegistry and main.py
def test_01_tool_registration_and_metadata():
    tool = DesktopScreenCaptureTool()
    assert tool.name == "desktop_screen_capture"
    assert tool.permission_level is PermissionLevel.CONFIRM
    assert tool.input_model is DesktopScreenCaptureInput

    registry = ToolRegistry()
    registry.register(tool)
    assert registry.get("desktop_screen_capture") is tool


# 2. Permission level enforcement: requires explicit confirmation
def test_02_permission_level_enforcement(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    tool = executor.registry.get("desktop_screen_capture")
    assert tool.permission_level is PermissionLevel.CONFIRM


# 3. Direct execution without approval is rejected (ApprovalRequiredError)
def test_03_direct_execution_without_approval_rejected(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("desktop_screen_capture", {}))

    events = executor.audit_events
    assert any(e.event_type == "approval_required" for e in events)


# 4. Successful authorized capture of primary display
def test_04_successful_authorized_capture_primary(tmp_path):
    executor, _, provider = make_screen_executor(tmp_path)
    res = approve_and_execute(executor, "desktop_screen_capture", {})

    assert "capture_id" in res
    assert res["display_id"] == "display-1"
    assert res["width"] == 1920
    assert res["height"] == 1080
    assert res["format"] == "PNG"
    assert res["size_bytes"] > 0
    assert "timestamp_utc" in res
    assert res["temp_file_path"] is None
    assert res["base64_data"] is None
    assert provider.capture_count == 1
    assert provider.last_bbox == (0, 0, 1920, 1080)


# 5. Capture specified monitor (e.g. display-2)
def test_05_capture_specified_monitor(tmp_path):
    executor, _, provider = make_screen_executor(tmp_path)
    res = approve_and_execute(executor, "desktop_screen_capture", {"display_id": "display-2"})

    assert res["display_id"] == "display-2"
    assert res["width"] == 1920
    assert res["height"] == 1080
    assert provider.last_bbox == (1920, 0, 3840, 1080)


# 6. Capture all displays combined
def test_06_capture_all_displays(tmp_path):
    executor, _, provider = make_screen_executor(tmp_path)
    res = approve_and_execute(executor, "desktop_screen_capture", {"all_displays": True})

    assert res["display_id"] == "all"
    assert res["width"] == 3840
    assert res["height"] == 1080
    assert provider.last_all_displays is True


# 7. Invalid monitor handling rejects nonexistent display ID
def test_07_invalid_monitor_handling_rejected(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    approval = executor.create_approval(
        "desktop_screen_capture",
        {"display_id": "display-999"},
        reason="test",
        requested_by="owner",
    )
    executor.resolve_approval(approval.id, approved=True)

    with pytest.raises(ToolExecutionError) as exc_info:
        asyncio.run(executor.execute("desktop_screen_capture", {"display_id": "display-999"}, approval_id=approval.id))
    assert "not found" in str(exc_info.value) or "not found" in str(exc_info.value.__cause__)


# 8. Temporary file lifecycle and cleanup
def test_08_temporary_file_lifecycle_and_cleanup(tmp_path):
    executor, controller, _ = make_screen_executor(tmp_path)
    res = approve_and_execute(executor, "desktop_screen_capture", {"save_to_temp": True})

    temp_path_str = res["temp_file_path"]
    assert temp_path_str is not None
    temp_path = Path(temp_path_str)
    assert temp_path.exists()
    assert temp_path.stat().st_size > 0

    # Cleanup individual capture
    capture_id = res["capture_id"]
    cleaned = controller.cleanup_capture(capture_id)
    assert cleaned is True
    assert not temp_path.exists()

    # Create another temp file and cleanup_all
    res2 = approve_and_execute(executor, "desktop_screen_capture", {"save_to_temp": True})
    p2 = Path(res2["temp_file_path"])
    assert p2.exists()
    all_cleaned = controller.cleanup_all()
    assert all_cleaned == 1
    assert not p2.exists()


# 9. Screenshot data is never written to Git-tracked directories
def test_09_screenshot_never_written_to_git_tracked_directories(tmp_path):
    executor, controller, _ = make_screen_executor(tmp_path)
    res = approve_and_execute(executor, "desktop_screen_capture", {"save_to_temp": True})

    temp_path = Path(res["temp_file_path"]).resolve()
    repo_root = Path(__file__).resolve().parent.parent

    # The temp path must NOT be located within the repo root
    # Note: tmp_path in pytest is outside repo root
    assert not str(temp_path).startswith(str(repo_root / "app"))
    assert not str(temp_path).startswith(str(repo_root / "data"))
    assert not str(temp_path).startswith(str(repo_root / "logs"))
    controller.cleanup_all()


# 10. Audit event generation and data minimization
def test_10_audit_event_generation_and_data_minimization(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    res = approve_and_execute(executor, "desktop_screen_capture", {"include_base64": True})

    events = executor.audit_events
    screen_events = [e for e in events if e.tool == "desktop_screen_capture"]
    types = [e.event_type for e in screen_events]

    assert "approval_created" in types
    assert "approval_resolved" in types
    assert "approval_consumed" in types
    assert "execution_started" in types
    assert "execution_succeeded" in types

    # Data minimization: Audit events must NEVER contain raw pixels, base64 strings, or image buffers
    audit_json = json.dumps([e.model_dump(mode="json") for e in events])
    assert res["base64_data"] not in audit_json
    assert "PNG" not in audit_json or "format" not in audit_json


# 11. In-memory base64 encoded PNG capture
def test_11_in_memory_base64_capture(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    res = approve_and_execute(executor, "desktop_screen_capture", {"include_base64": True})

    b64 = res["base64_data"]
    assert b64 is not None
    raw_bytes = base64.b64decode(b64)
    # Check PNG magic bytes: \x89PNG\r\n\x1a\n
    assert raw_bytes.startswith(b"\x89PNG\r\n\x1a\n")


# 12. Single-use approval enforcement (cannot reuse approval)
def test_12_single_use_approval_enforcement(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    approval = executor.create_approval("desktop_screen_capture", {}, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    # First consumption succeeds
    res = asyncio.run(executor.execute("desktop_screen_capture", {}, approval_id=approval.id))
    assert "capture_id" in res

    # Second consumption fails
    with pytest.raises(ApprovalConsumedError):
        asyncio.run(executor.execute("desktop_screen_capture", {}, approval_id=approval.id))


# 13. Approval parameter mismatch is rejected
def test_13_approval_parameter_mismatch_rejected(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    approval = executor.create_approval("desktop_screen_capture", {"display_id": "display-1"}, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    # Attempt execution with different display parameter
    with pytest.raises(ApprovalMismatchError):
        asyncio.run(executor.execute("desktop_screen_capture", {"display_id": "display-2"}, approval_id=approval.id))


# 14. Expired approval is rejected
def test_14_expired_approval_rejected(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    approval = executor.create_approval("desktop_screen_capture", {}, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    executor.now = lambda: datetime.now(timezone.utc) + timedelta(minutes=10)
    with pytest.raises(ApprovalExpiredError):
        asyncio.run(executor.execute("desktop_screen_capture", {}, approval_id=approval.id))


# 15. Rejected approval cannot be executed
def test_15_rejected_approval_cannot_execute(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    approval = executor.create_approval("desktop_screen_capture", {}, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=False)

    with pytest.raises(ApprovalRejectedError):
        asyncio.run(executor.execute("desktop_screen_capture", {}, approval_id=approval.id))


# 16. RBAC enforcement via API
def test_16_api_rbac_enforcement(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(tmp_path / "jarvis.sqlite3"))
    monkeypatch.setenv(
        "JARVIS_AUTH_TOKENS",
        json.dumps([
            {"subject": "owner", "role": "owner", "token": "test-owner-token-stage10c"},
            {"subject": "auditor", "role": "auditor", "token": "test-auditor-token-stage10c"},
            {"subject": "readonly", "role": "read_only", "token": "test-readonly-token-stage10c"},
        ]),
    )
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    # Unauthenticated -> 401
    assert client.post("/actions/desktop_screen_capture/execute", json={"input": {}}).status_code == 401

    # Read-only -> 403
    ro_headers = {"Authorization": "Bearer test-readonly-token-stage10c"}
    assert client.post("/actions/desktop_screen_capture/execute", headers=ro_headers, json={"input": {}}).status_code == 403

    # Auditor -> 403
    auditor_headers = {"Authorization": "Bearer test-auditor-token-stage10c"}
    assert client.post("/actions/desktop_screen_capture/execute", headers=auditor_headers, json={"input": {}}).status_code == 403

    # Owner -> 409 (Approval required)
    owner_headers = {"Authorization": "Bearer test-owner-token-stage10c"}
    res = client.post("/actions/desktop_screen_capture/execute", headers=owner_headers, json={"input": {}})
    assert res.status_code == 409


# 17. Agent state machine workflow for screen capture
def test_17_agent_state_machine_workflow(tmp_path):
    executor, _, _ = make_screen_executor(tmp_path)
    agent = Agent(executor=executor)

    state = agent.prepare("Capture screen for analysis")
    payload = {"all_displays": False}

    # Attempting to execute enters WAITING_FOR_APPROVAL
    state = asyncio.run(agent.execute_step(state, "desktop_screen_capture", payload))
    assert state.status is AgentStatus.WAITING_FOR_APPROVAL
    assert state.approval_id is not None

    # Resolve approval
    executor.resolve_approval(state.approval_id, approved=True)

    # Resume agent
    resumed = asyncio.run(agent.resume(state, payload))
    assert resumed.status is AgentStatus.COMPLETED
    assert "capture_id" in resumed.result


# 18. OCR provider abstraction and sensitive credential protection
def test_18_ocr_abstraction_and_sensitive_data_protection():
    canned_text = [
        OCRTextElement(text="Welcome to Dashboard", confidence=0.98),
        OCRTextElement(text="Active Task: Document Processing", confidence=0.95),
    ]
    ocr_provider = MockOCRProvider(canned_elements=canned_text)
    assert isinstance(ocr_provider, OCRProvider)

    result = ocr_provider.extract_text(image=None)
    assert isinstance(result, OCRResult)
    assert result.element_count == 2
    assert "Dashboard" in result.full_text

    # Verify no credentials leaked
    assert "secret_token" not in result.full_text
    assert "password" not in result.full_text


# 19. Vision provider abstraction without network calls
def test_19_vision_abstraction_no_network_calls():
    vision = MockVisionProvider(description="Safe desktop overview")
    assert isinstance(vision, VisionProvider)

    analysis = vision.analyze(image=None, prompt="Describe layout")
    assert analysis.description == "Safe desktop overview"
    assert len(analysis.layout_elements) == 1
    assert vision.call_count == 1


# 20. Windows native screen capture provider instantiates cleanly
def test_20_windows_native_provider_instantiation():
    provider = WindowsScreenCaptureProvider()
    assert isinstance(provider, ScreenCaptureProvider)
    assert hasattr(provider, "capture_screen")


# 21. Factory function creates configured controller
def test_21_factory_function_creates_configured_controller():
    controller = create_default_desktop_screen_controller()
    assert isinstance(controller, DesktopScreenController)
    assert hasattr(controller, "capture")
    assert hasattr(controller, "cleanup_capture")
    assert hasattr(controller, "cleanup_all")
