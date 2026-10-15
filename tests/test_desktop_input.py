"""Comprehensive test suite for Stage 10B: Controlled Mouse, Keyboard & Safe Hotkey Input."""
from __future__ import annotations

import asyncio
import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.agent.agent import Agent
from app.agent.state import AgentStatus
from app.core.config import get_settings
from app.desktop.input import (
    ALLOWED_HOTKEYS,
    FORBIDDEN_HOTKEYS,
    DesktopInputController,
    InvalidHotkeyError,
    MockDesktopInputDriver,
    MouseCoordinatesOutOfBoundsError,
    SensitiveInputError,
    WindowsDesktopInputDriver,
    validate_desktop_text,
    validate_mouse_coordinates,
    validate_safe_hotkey,
)
from app.desktop.inspector import DesktopInspector, MockDesktopDriver
from app.desktop.models import DisplayBounds, DisplayInfo
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.desktop_hotkey import DesktopHotkeyInput, DesktopHotkeyTool
from app.tools.desktop_keyboard_type import DesktopKeyboardTypeInput, DesktopKeyboardTypeTool
from app.tools.desktop_mouse_click import DesktopMouseClickInput, DesktopMouseClickTool
from app.tools.desktop_mouse_move import DesktopMouseMoveInput, DesktopMouseMoveTool
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


def make_mock_inspector(displays: list[DisplayInfo] | None = None) -> DesktopInspector:
    if displays is None:
        displays = [
            DisplayInfo(
                id="display-1",
                is_primary=True,
                bounds=DisplayBounds(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080),
                work_area=DisplayBounds(left=0, top=0, right=1920, bottom=1040, width=1920, height=1040),
                scale_factor=1.0,
            )
        ]
    driver = MockDesktopDriver(windows=[], displays=displays)
    return DesktopInspector(driver=driver)


def make_input_executor(
    tmp_path,
    inspector: DesktopInspector | None = None,
    simulate_verification_failure: bool = False,
) -> tuple[Executor, MockDesktopInputDriver]:
    inspector = inspector or make_mock_inspector()
    driver = MockDesktopInputDriver(simulate_verification_failure=simulate_verification_failure)
    controller = DesktopInputController(inspector=inspector, driver=driver)

    repo = SQLiteRepository(tmp_path / "jarvis_input_test.sqlite3")
    registry = ToolRegistry()
    registry.register(DesktopMouseMoveTool(inspector=inspector, controller=controller))
    registry.register(DesktopMouseClickTool(inspector=inspector, controller=controller))
    registry.register(DesktopKeyboardTypeTool(inspector=inspector, controller=controller))
    registry.register(DesktopHotkeyTool(inspector=inspector, controller=controller))
    return Executor(registry, repo), driver


def approve_and_execute(executor: Executor, tool_name: str, payload: dict) -> dict[str, object]:
    approval = executor.create_approval(tool_name, payload, reason="Test input approval", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)
    return asyncio.run(executor.execute(tool_name, payload, approval_id=approval.id))


# 1. All Stage 10B tools are registered correctly in application
def test_01_stage_10b_tools_registered_in_app():
    app = create_app()
    registry: ToolRegistry = app.state.executor.registry
    tools = {t.name: t for t in registry.list_tools()}

    assert "desktop_mouse_move" in tools
    assert "desktop_mouse_click" in tools
    assert "desktop_keyboard_type" in tools
    assert "desktop_hotkey" in tools


# 2. Correct permission level is enforced (PermissionLevel.CONFIRM)
def test_02_permission_level_confirm_enforced():
    app = create_app()
    registry: ToolRegistry = app.state.executor.registry
    tools = {t.name: t for t in registry.list_tools()}

    assert tools["desktop_mouse_move"].permission_level is PermissionLevel.CONFIRM
    assert tools["desktop_mouse_click"].permission_level is PermissionLevel.CONFIRM
    assert tools["desktop_keyboard_type"].permission_level is PermissionLevel.CONFIRM
    assert tools["desktop_hotkey"].permission_level is PermissionLevel.CONFIRM


# 3. Execution without required approval fails (ApprovalRequiredError)
def test_03_execution_without_approval_fails(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("desktop_mouse_move", {"x": 500, "y": 300}))

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("desktop_mouse_click", {"click_type": "left_click"}))

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("desktop_keyboard_type", {"text": "Hello world"}))

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("desktop_hotkey", {"hotkey": "CTRL+C"}))


# 4. Invalid mouse coordinates are rejected by schema validation
def test_04_invalid_mouse_coordinates_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    # String instead of int
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_mouse_move", {"x": "invalid", "y": 100}, reason="test", requested_by="owner")

    # Missing coordinate in move
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_mouse_move", {"x": 100}, reason="test", requested_by="owner")

    # Mismatched coordinates in click (x provided, y omitted)
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_mouse_click", {"x": 100}, reason="test", requested_by="owner")


# 5. Out-of-bounds coordinates are rejected against display work area
def test_05_out_of_bounds_coordinates_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    # Beyond display bounds (width=1920, height=1040 work area)
    with pytest.raises(ToolExecutionError) as exc_info:
        approve_and_execute(executor, "desktop_mouse_move", {"x": 5000, "y": 5000})
    err_msg = (str(exc_info.value) + " " + str(exc_info.value.__cause__)).lower()
    assert "outside" in err_msg

    # Negative coordinates on standard single monitor starting at (0, 0)
    with pytest.raises(ToolExecutionError) as exc_info:
        approve_and_execute(executor, "desktop_mouse_move", {"x": -50, "y": 200})
    err_msg = (str(exc_info.value) + " " + str(exc_info.value.__cause__)).lower()
    assert "outside" in err_msg

    # Click with out of bounds coordinates
    with pytest.raises(ToolExecutionError) as exc_info:
        approve_and_execute(executor, "desktop_mouse_click", {"x": 2500, "y": 500, "click_type": "left_click"})
    err_msg = (str(exc_info.value) + " " + str(exc_info.value.__cause__)).lower()
    assert "outside" in err_msg


# Multi-monitor safe negative coordinates support
def test_05b_multi_monitor_negative_coordinates_handled_safely(tmp_path):
    # Two monitors: primary (0..1920) and left monitor (-1920..0)
    displays = [
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
            bounds=DisplayBounds(left=-1920, top=0, right=0, bottom=1080, width=1920, height=1080),
            work_area=DisplayBounds(left=-1920, top=0, right=0, bottom=1040, width=1920, height=1040),
            scale_factor=1.0,
        ),
    ]
    inspector = make_mock_inspector(displays=displays)
    executor, driver = make_input_executor(tmp_path, inspector=inspector)

    # Negative coordinate inside left monitor work area succeeds
    res = approve_and_execute(executor, "desktop_mouse_move", {"x": -500, "y": 300})
    assert res["success"] is True
    assert res["verified"] is True
    assert driver.cursor_position == (-500, 300)

    # Beyond left monitor (-2500) fails
    with pytest.raises(ToolExecutionError):
        approve_and_execute(executor, "desktop_mouse_move", {"x": -2500, "y": 300})


# 6. Valid mouse movement succeeds through the mock driver
def test_06_valid_mouse_movement_succeeds(tmp_path):
    executor, driver = make_input_executor(tmp_path)

    res = approve_and_execute(executor, "desktop_mouse_move", {"x": 400, "y": 300})
    assert res["success"] is True
    assert res["verified"] is True
    assert res["action"] == "desktop_mouse_move"
    assert res["details"]["x"] == 400
    assert res["details"]["y"] == 300
    assert driver.cursor_position == (400, 300)


# 7. Valid click succeeds through the mock driver (left, right, double_left)
def test_07_valid_click_succeeds(tmp_path):
    executor, driver = make_input_executor(tmp_path)

    # Left click with coordinates
    res_left = approve_and_execute(executor, "desktop_mouse_click", {"x": 350, "y": 250, "click_type": "left_click"})
    assert res_left["success"] is True
    assert res_left["verified"] is True
    assert driver.cursor_position == (350, 250)
    assert driver.clicks[-1] == {"click_type": "left_click", "position": (350, 250)}

    # Right click using button alias
    res_right = approve_and_execute(executor, "desktop_mouse_click", {"button": "right"})
    assert res_right["success"] is True
    assert res_right["verified"] is True
    assert driver.clicks[-1]["click_type"] == "right_click"

    # Double left click
    res_double = approve_and_execute(executor, "desktop_mouse_click", {"click_type": "double_left_click"})
    assert res_double["success"] is True
    assert res_double["verified"] is True
    assert driver.clicks[-1]["click_type"] == "double_left_click"


# 8. Unsupported click type is rejected
def test_08_unsupported_click_type_is_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_mouse_click", {"click_type": "middle_click"}, reason="test", requested_by="owner")

    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_mouse_click", {"click_type": "triple_click"}, reason="test", requested_by="owner")

    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_mouse_click", {"button": "scroll_wheel"}, reason="test", requested_by="owner")


# 9. Excessively long text input is rejected
def test_09_excessively_long_text_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    # 501 characters exceeds max_length=500
    long_text = "a" * 501
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_keyboard_type", {"text": long_text}, reason="test", requested_by="owner")

    # Empty text rejected
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_keyboard_type", {"text": ""}, reason="test", requested_by="owner")

    # Control chars (e.g. null byte) rejected
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_keyboard_type", {"text": "hello\x00world"}, reason="test", requested_by="owner")


# 10. Valid text input reaches the mock driver exactly as supplied
def test_10_valid_text_input_reaches_driver_exactly(tmp_path):
    executor, driver = make_input_executor(tmp_path)
    sample_text = "Standard text typing without secrets 123!"

    res = approve_and_execute(executor, "desktop_keyboard_type", {"text": sample_text})
    assert res["success"] is True
    assert res["verified"] is True
    assert res["details"]["text_length"] == len(sample_text)
    assert driver.typed_text[-1] == sample_text


# 11. Clipboard is never read automatically
def test_11_clipboard_is_never_read_automatically():
    # Verify the desktop input tools do not import or invoke clipboard readers
    import app.desktop.input as input_module
    import app.tools.desktop_keyboard_type as kb_module

    assert not hasattr(input_module, "pyperclip")
    assert not hasattr(input_module, "OpenClipboard")
    assert not hasattr(kb_module, "pyperclip")
    assert not hasattr(kb_module, "OpenClipboard")


# 12. Credential/environment-variable extraction is impossible through the tool interface
def test_12_credential_and_env_extraction_impossible(tmp_path, monkeypatch):
    test_dummy_key = "sk_test_dummy_key_for_unit_tests"
    monkeypatch.setenv("SECRET_API_TOKEN", "mock_dummy_secret_env_token_value_12345")
    executor, _ = make_input_executor(tmp_path)

    # Direct typing of credentials rejected by sensitive pattern detector
    with pytest.raises(InvalidToolInputError):
        executor.create_approval(
            "desktop_keyboard_type",
            {"text": "my_secret_password = super_secret"},
            reason="test",
            requested_by="owner",
        )

    with pytest.raises(InvalidToolInputError):
        executor.create_approval(
            "desktop_keyboard_type",
            {"text": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"},
            reason="test",
            requested_by="owner",
        )

    with pytest.raises(InvalidToolInputError):
        executor.create_approval(
            "desktop_keyboard_type",
            {"text": test_dummy_key},
            reason="test",
            requested_by="owner",
        )

    # Passing env var literal string does not evaluate or extract the secret
    res = approve_and_execute(executor, "desktop_keyboard_type", {"text": "$SECRET_API_TOKEN"})
    assert res["success"] is True
    # The text typed is literal "$SECRET_API_TOKEN", not the value of the environment variable
    assert res["details"]["text_length"] == len("$SECRET_API_TOKEN")


# 13. Allowlisted hotkeys succeed
def test_13_allowlisted_hotkeys_succeed(tmp_path):
    executor, driver = make_input_executor(tmp_path)

    safe_samples = ["CTRL+C", "CTRL+V", "CTRL+A", "CTRL+Z", "ALT+TAB", "ESC", "ENTER", "BACKSPACE"]
    for hk in safe_samples:
        res = approve_and_execute(executor, "desktop_hotkey", {"hotkey": hk})
        assert res["success"] is True
        assert res["verified"] is True
        assert res["details"]["hotkey"] == hk
        assert driver.hotkeys[-1] == hk

    # Case-insensitive normalization (e.g. lowercase 'ctrl+c' normalizes to 'CTRL+C')
    res_lower = approve_and_execute(executor, "desktop_hotkey", {"hotkey": "ctrl+c"})
    assert res_lower["success"] is True
    assert res_lower["details"]["hotkey"] == "CTRL+C"


# 14. Non-allowlisted hotkeys are rejected
def test_14_non_allowlisted_hotkeys_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_hotkey", {"hotkey": "CTRL+SHIFT+P"}, reason="test", requested_by="owner")

    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_hotkey", {"hotkey": "WIN+D"}, reason="test", requested_by="owner")

    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_hotkey", {"hotkey": "ALT+F1"}, reason="test", requested_by="owner")


# 15. Destructive/system-sensitive hotkeys are rejected
def test_15_destructive_hotkeys_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    destructive = ["CTRL+ALT+DELETE", "CTRL+ALT+DEL", "ALT+F4", "CTRL+SHIFT+ESC", "WIN+L", "CTRL+W"]
    for hk in destructive:
        with pytest.raises(InvalidToolInputError):
            executor.create_approval("desktop_hotkey", {"hotkey": hk}, reason="test", requested_by="owner")


# 16. Approval parameter mismatch is rejected
def test_16_approval_parameter_mismatch_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    approval = executor.create_approval("desktop_mouse_move", {"x": 200, "y": 200}, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    # Attempt to execute with modified coordinates
    with pytest.raises(ApprovalMismatchError):
        asyncio.run(executor.execute("desktop_mouse_move", {"x": 300, "y": 300}, approval_id=approval.id))


# 17. Approval reuse is rejected (single-use approval)
def test_17_approval_reuse_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    payload = {"x": 200, "y": 200}
    approval = executor.create_approval("desktop_mouse_move", payload, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    # First consumption succeeds
    res = asyncio.run(executor.execute("desktop_mouse_move", payload, approval_id=approval.id))
    assert res["success"] is True

    # Second consumption fails
    with pytest.raises(ApprovalConsumedError):
        asyncio.run(executor.execute("desktop_mouse_move", payload, approval_id=approval.id))


# 18. Expired approval is rejected
def test_18_expired_approval_rejected(tmp_path):
    executor, _ = make_input_executor(tmp_path)

    payload = {"x": 200, "y": 200}
    approval = executor.create_approval("desktop_mouse_move", payload, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    # Fast forward past TTL
    executor.now = lambda: datetime.now(timezone.utc) + timedelta(minutes=10)

    with pytest.raises(ApprovalExpiredError):
        asyncio.run(executor.execute("desktop_mouse_move", payload, approval_id=approval.id))


# 19. Verification failure is handled correctly
def test_19_verification_failure_handling(tmp_path):
    executor, _ = make_input_executor(tmp_path, simulate_verification_failure=True)

    with pytest.raises(ToolExecutionError) as exc_info:
        approve_and_execute(executor, "desktop_mouse_move", {"x": 200, "y": 200})
    err_msg = (str(exc_info.value) + " " + str(exc_info.value.__cause__)).lower()
    assert "verification failed" in err_msg

    # Verify execution_failed audit event was emitted
    events = executor.audit_events
    move_events = [e for e in events if e.tool == "desktop_mouse_move"]
    assert any(e.event_type == "execution_failed" for e in move_events)


# 20. Audit events are generated correctly and raw text excluded
def test_20_audit_events_lifecycle_and_data_minimization(tmp_path):
    executor, _ = make_input_executor(tmp_path)
    text_payload = {"text": "Safe message for audit verification"}

    approve_and_execute(executor, "desktop_keyboard_type", text_payload)

    events = executor.audit_events
    kb_events = [e for e in events if e.tool == "desktop_keyboard_type"]
    types = [e.event_type for e in kb_events]

    assert "approval_created" in types
    assert "approval_resolved" in types
    assert "approval_consumed" in types
    assert "execution_started" in types
    assert "execution_succeeded" in types

    # Data minimization: verify raw text was not logged into audit events
    audit_json = json.dumps([e.model_dump(mode="json") for e in executor.audit_events])
    assert "Safe message for audit verification" not in audit_json


# 21. RBAC remains enforced on all Stage 10B tools
def test_21_api_rbac_enforcement(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(tmp_path / "jarvis.sqlite3"))
    monkeypatch.setenv(
        "JARVIS_AUTH_TOKENS",
        json.dumps([
            {"subject": "owner", "role": "owner", "token": "test-owner-token-stage10b"},
            {"subject": "auditor", "role": "auditor", "token": "test-auditor-token-stage10b"},
        ]),
    )
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    # Unauthenticated -> 401
    assert client.post("/actions/desktop_mouse_move/execute", json={"input": {"x": 100, "y": 100}}).status_code == 401

    # Auditor -> 403
    auditor_headers = {"Authorization": "Bearer test-auditor-token-stage10b"}
    assert client.post("/actions/desktop_mouse_move/execute", headers=auditor_headers, json={"input": {"x": 100, "y": 100}}).status_code == 403

    # Owner -> 409 (Approval required)
    owner_headers = {"Authorization": "Bearer test-owner-token-stage10b"}
    res = client.post("/actions/desktop_mouse_move/execute", headers=owner_headers, json={"input": {"x": 100, "y": 100}})
    assert res.status_code == 409


# 22. Agent state machine approval and execution workflow for Stage 10B
def test_22_agent_state_machine_workflow(tmp_path):
    executor, driver = make_input_executor(tmp_path)
    agent = Agent(executor=executor)

    state = agent.prepare("Type a command")
    payload = {"text": "ls -la"}

    # Attempting to execute enters WAITING_FOR_APPROVAL
    state = asyncio.run(agent.execute_step(state, "desktop_keyboard_type", payload))
    assert state.status is AgentStatus.WAITING_FOR_APPROVAL
    assert state.approval_id is not None

    # Resolve approval
    executor.resolve_approval(state.approval_id, approved=True)

    # Resume agent
    resumed = asyncio.run(agent.resume(state, payload))
    assert resumed.status is AgentStatus.COMPLETED
    assert resumed.result["success"] is True
    assert driver.typed_text[-1] == "ls -la"


# 23. Windows native input driver instantiates cleanly
def test_23_windows_desktop_input_driver_instantiation():
    driver = WindowsDesktopInputDriver()
    assert hasattr(driver, "move_cursor")
    assert hasattr(driver, "click")
    assert hasattr(driver, "type_text")
    assert hasattr(driver, "press_hotkey")
