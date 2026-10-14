"""Comprehensive test suite for Phase 10A controlled window state and layout actions."""
from __future__ import annotations

import asyncio
import json
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.agent import Agent
from app.agent.state import AgentStatus
from app.core.config import get_settings
from app.desktop.actions import (
    DesktopActionResult,
    MockDesktopActionController,
    TargetWindowNotFoundError,
    WindowBoundsError,
    WindowsDesktopActionController,
    find_target_window,
    validate_move_resize_bounds,
)
from app.desktop.inspector import DesktopInspector, MockDesktopDriver
from app.desktop.models import (
    ActiveWindow,
    DesktopWindow,
    DisplayBounds,
    DisplayInfo,
    WindowBounds,
)
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.desktop_window_focus import DesktopWindowFocusInput, DesktopWindowFocusTool
from app.tools.desktop_window_move_resize import DesktopWindowMoveResizeInput, DesktopWindowMoveResizeTool
from app.tools.desktop_window_state import DesktopWindowStateInput, DesktopWindowStateTool
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


def make_mock_inspector() -> DesktopInspector:
    windows = [
        DesktopWindow(
            window_id="win-0x1001",
            title="Editor - Visual Studio Code",
            process_id=1111,
            process_name="code.exe",
            is_minimized=False,
            bounds=WindowBounds(left=100, top=100, width=1000, height=700),
        ),
        DesktopWindow(
            window_id="win-0x1002",
            title="Terminal - PowerShell",
            process_id=2222,
            process_name="powershell.exe",
            is_minimized=True,
            bounds=WindowBounds(left=50, top=50, width=800, height=500),
        ),
        DesktopWindow(
            window_id="win-0x1003",
            title="Calculator",
            process_id=3333,
            process_name="calculator.exe",
            is_minimized=False,
            bounds=WindowBounds(left=300, top=300, width=400, height=600),
        ),
    ]
    displays = [
        DisplayInfo(
            id="display-1",
            is_primary=True,
            bounds=DisplayBounds(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080),
            work_area=DisplayBounds(left=0, top=0, right=1920, bottom=1040, width=1920, height=1040),
            scale_factor=1.0,
        )
    ]
    driver = MockDesktopDriver(windows=windows, displays=displays)
    return DesktopInspector(driver=driver)


def make_executor(tmp_path, inspector=None, simulate_verification_failure=False) -> tuple[Executor, MockDesktopActionController]:
    inspector = inspector or make_mock_inspector()
    controller = MockDesktopActionController(
        inspector=inspector,
        simulate_verification_failure=simulate_verification_failure,
    )
    repo = SQLiteRepository(tmp_path / "jarvis_actions_test.sqlite3")
    registry = ToolRegistry()
    registry.register(DesktopWindowFocusTool(inspector=inspector, controller=controller))
    registry.register(DesktopWindowStateTool(inspector=inspector, controller=controller))
    registry.register(DesktopWindowMoveResizeTool(inspector=inspector, controller=controller))
    return Executor(registry, repo), controller


def approve_and_execute(executor: Executor, tool_name: str, payload: dict) -> dict[str, object]:
    approval = executor.create_approval(tool_name, payload, reason="Test approval", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)
    return asyncio.run(executor.execute(tool_name, payload, approval_id=approval.id))


# 1. Tools registered in application and declare PermissionLevel.CONFIRM
def test_01_tools_registered_with_confirm_permission():
    app = create_app()
    registry: ToolRegistry = app.state.executor.registry
    tools = {t.name: t for t in registry.list_tools()}
    assert "desktop_window_focus" in tools
    assert "desktop_window_state" in tools
    assert "desktop_window_move_resize" in tools

    assert tools["desktop_window_focus"].permission_level is PermissionLevel.CONFIRM
    assert tools["desktop_window_state"].permission_level is PermissionLevel.CONFIRM
    assert tools["desktop_window_move_resize"].permission_level is PermissionLevel.CONFIRM


# 2. Execution without approval raises ApprovalRequiredError
def test_02_execution_without_approval_raises_approval_required(tmp_path):
    executor, _ = make_executor(tmp_path)
    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("desktop_window_focus", {"window_id": "win-0x1001"}))

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("desktop_window_state", {"window_id": "win-0x1001", "operation": "minimize"}))

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("desktop_window_move_resize", {"window_id": "win-0x1001", "left": 200}))


# 3. Window focus success and post-action verification
def test_03_window_focus_success_and_verification(tmp_path):
    executor, controller = make_executor(tmp_path)
    payload = {"window_id": "win-0x1001"}
    res = approve_and_execute(executor, "desktop_window_focus", payload)
    assert res["success"] is True
    assert res["verified"] is True
    assert res["action"] == "desktop_window_focus"
    assert res["window_id"] == "win-0x1001"
    assert controller.focused_window_id == "win-0x1001"


# 4. Window focus on nonexistent window fails
def test_04_window_focus_nonexistent_window_fails(tmp_path):
    executor, _ = make_executor(tmp_path)
    payload = {"window_id": "win-0x9999"}
    with pytest.raises(ToolExecutionError) as exc_info:
        approve_and_execute(executor, "desktop_window_focus", payload)
    err_msg = (str(exc_info.value) + " " + str(exc_info.value.__cause__)).lower()
    assert "not found" in err_msg


# 5. Window state minimize, maximize, and restore
def test_05_window_state_transitions(tmp_path):
    executor, controller = make_executor(tmp_path)

    # Minimize
    res_min = approve_and_execute(executor, "desktop_window_state", {"window_id": "win-0x1001", "operation": "minimize"})
    assert res_min["success"] is True
    assert res_min["verified"] is True
    assert controller.window_states["win-0x1001"] == "minimize"

    # Maximize
    res_max = approve_and_execute(executor, "desktop_window_state", {"window_id": "win-0x1001", "operation": "maximize"})
    assert res_max["success"] is True
    assert res_max["verified"] is True
    assert controller.window_states["win-0x1001"] == "maximize"

    # Restore
    res_res = approve_and_execute(executor, "desktop_window_state", {"window_id": "win-0x1001", "operation": "restore"})
    assert res_res["success"] is True
    assert res_res["verified"] is True
    assert controller.window_states["win-0x1001"] == "restore"


# 6. Window state rejects invalid operations via schema validation
def test_06_window_state_rejects_invalid_operations(tmp_path):
    executor, _ = make_executor(tmp_path)
    # Invalid operation 'close' or 'kill' rejected before approval
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_window_state", {"window_id": "win-0x1001", "operation": "close"}, reason="test", requested_by="owner")

    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_window_state", {"window_id": "win-0x1001", "operation": "kill"}, reason="test", requested_by="owner")


# 7. Window move and resize inside valid display bounds
def test_07_window_move_resize_inside_bounds(tmp_path):
    executor, controller = make_executor(tmp_path)
    payload = {"window_id": "win-0x1001", "left": 250, "top": 150, "width": 800, "height": 600}
    res = approve_and_execute(executor, "desktop_window_move_resize", payload)
    assert res["success"] is True
    assert res["verified"] is True
    assert res["details"]["left"] == 250
    assert res["details"]["top"] == 150
    assert res["details"]["width"] == 800
    assert res["details"]["height"] == 600
    assert controller.window_geometries["win-0x1001"].width == 800


# 8. Window move/resize coordinates outside display bounds are rejected
def test_08_window_move_resize_outside_bounds_rejected(tmp_path):
    executor, _ = make_executor(tmp_path)
    # Outside monitor work area (e.g. left = 5000)
    payload = {"window_id": "win-0x1001", "left": 5000, "top": 5000}
    with pytest.raises(ToolExecutionError) as exc_info:
        approve_and_execute(executor, "desktop_window_move_resize", payload)
    err_msg = (str(exc_info.value) + " " + str(exc_info.value.__cause__)).lower()
    assert "outside" in err_msg


# 9. Window move/resize minimum dimensions enforced
def test_09_window_move_resize_minimum_dimensions_enforced(tmp_path):
    executor, _ = make_executor(tmp_path)
    # Width < 200 rejected by Pydantic schema validation
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_window_move_resize", {"window_id": "win-0x1001", "width": 150}, reason="test", requested_by="owner")

    # Height < 150 rejected by Pydantic schema validation
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_window_move_resize", {"window_id": "win-0x1001", "height": 100}, reason="test", requested_by="owner")

    # At least one field required
    with pytest.raises(InvalidToolInputError):
        executor.create_approval("desktop_window_move_resize", {"window_id": "win-0x1001"}, reason="test", requested_by="owner")


# 10. Moving or resizing a minimized window is rejected
def test_10_moving_minimized_window_rejected(tmp_path):
    executor, _ = make_executor(tmp_path)
    # win-0x1002 is minimized in make_mock_inspector
    payload = {"window_id": "win-0x1002", "left": 200, "top": 200}
    with pytest.raises(ToolExecutionError) as exc_info:
        approve_and_execute(executor, "desktop_window_move_resize", payload)
    err_msg = (str(exc_info.value) + " " + str(exc_info.value.__cause__)).lower()
    assert "minimized" in err_msg


# 11. Verification failure handling reports verified=False and raises ToolExecutionError
def test_11_verification_failure_handling(tmp_path):
    executor, _ = make_executor(tmp_path, simulate_verification_failure=True)
    payload = {"window_id": "win-0x1001"}
    with pytest.raises(ToolExecutionError) as exc_info:
        approve_and_execute(executor, "desktop_window_focus", payload)
    err_msg = (str(exc_info.value) + " " + str(exc_info.value.__cause__)).lower()
    assert "verification failed" in err_msg

    # Verify execution_failed audit event was recorded
    events = executor.audit_events
    focus_events = [e for e in events if e.tool == "desktop_window_focus"]
    assert any(e.event_type == "execution_failed" for e in focus_events)


# 12. Audit event trail records full lifecycle
def test_12_audit_events_record_full_lifecycle(tmp_path):
    executor, _ = make_executor(tmp_path)
    payload = {"window_id": "win-0x1001"}
    approve_and_execute(executor, "desktop_window_focus", payload)

    events = executor.audit_events
    focus_events = [e for e in events if e.tool == "desktop_window_focus"]
    event_types = [e.event_type for e in focus_events]

    assert "approval_created" in event_types
    assert "approval_resolved" in event_types
    assert "approval_consumed" in event_types
    assert "execution_started" in event_types
    assert "execution_succeeded" in event_types


# 13. Single-use atomic approval consumption prevents reuse
def test_13_single_use_approval_prevents_reuse(tmp_path):
    executor, _ = make_executor(tmp_path)
    payload = {"window_id": "win-0x1001"}
    approval = executor.create_approval("desktop_window_focus", payload, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    # First execution succeeds
    res = asyncio.run(executor.execute("desktop_window_focus", payload, approval_id=approval.id))
    assert res["success"] is True

    # Second execution with same approval fails with ApprovalConsumedError
    with pytest.raises(ApprovalConsumedError):
        asyncio.run(executor.execute("desktop_window_focus", payload, approval_id=approval.id))


# 14. Approval mismatch prevents execution with altered parameters
def test_14_approval_mismatch_prevents_altered_execution(tmp_path):
    executor, _ = make_executor(tmp_path)
    approval = executor.create_approval("desktop_window_focus", {"window_id": "win-0x1001"}, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    # Attempt to execute on a different window using the same approval
    with pytest.raises(ApprovalMismatchError):
        asyncio.run(executor.execute("desktop_window_focus", {"window_id": "win-0x1003"}, approval_id=approval.id))


# 15. Expired approval cannot be consumed
def test_15_expired_approval_cannot_be_consumed(tmp_path):
    executor, _ = make_executor(tmp_path)
    payload = {"window_id": "win-0x1001"}
    approval = executor.create_approval("desktop_window_focus", payload, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=True)

    # Fast forward executor time past approval TTL
    executor.now = lambda: datetime.now(timezone.utc) + timedelta(minutes=10)

    with pytest.raises(ApprovalExpiredError):
        asyncio.run(executor.execute("desktop_window_focus", payload, approval_id=approval.id))


# 16. Rejected approval cannot be executed
def test_16_rejected_approval_cannot_be_executed(tmp_path):
    executor, _ = make_executor(tmp_path)
    payload = {"window_id": "win-0x1001"}
    approval = executor.create_approval("desktop_window_focus", payload, reason="test", requested_by="owner")
    executor.resolve_approval(approval.id, approved=False)

    with pytest.raises(ApprovalRejectedError):
        asyncio.run(executor.execute("desktop_window_focus", payload, approval_id=approval.id))


# 17. API authentication and RBAC enforcement on desktop action tools
def test_17_api_rbac_enforcement(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(tmp_path / "jarvis.sqlite3"))
    monkeypatch.setenv(
        "JARVIS_AUTH_TOKENS",
        json.dumps([
            {"subject": "owner", "role": "owner", "token": "test-owner-token-actions"},
            {"subject": "auditor", "role": "auditor", "token": "test-auditor-token-actions"},
        ]),
    )
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    # Missing auth -> 401
    assert client.post("/actions/desktop_window_focus/execute", json={"input": {"window_id": "win-0x1001"}}).status_code == 401

    # Auditor -> 403
    auditor_headers = {"Authorization": "Bearer test-auditor-token-actions"}
    assert client.post("/actions/desktop_window_focus/execute", headers=auditor_headers, json={"input": {"window_id": "win-0x1001"}}).status_code == 403

    # Owner -> 409 (Approval required, confirming route reached and validated)
    owner_headers = {"Authorization": "Bearer test-owner-token-actions"}
    res = client.post("/actions/desktop_window_focus/execute", headers=owner_headers, json={"input": {"window_id": "win-0x1001"}})
    assert res.status_code == 409


# 18. Agent state machine approval workflow for desktop actions
def test_18_agent_state_machine_approval_workflow(tmp_path):
    executor, _ = make_executor(tmp_path)
    agent = Agent(executor=executor)

    state = agent.prepare("Focus the editor window")
    payload = {"window_id": "win-0x1001"}

    # Attempting to execute enters WAITING_FOR_APPROVAL
    state = asyncio.run(agent.execute_step(state, "desktop_window_focus", payload))
    assert state.status is AgentStatus.WAITING_FOR_APPROVAL
    assert state.approval_id is not None

    # Resolve approval
    executor.resolve_approval(state.approval_id, approved=True)

    # Resume agent
    resumed_state = asyncio.run(agent.resume(state, payload))
    assert resumed_state.status is AgentStatus.COMPLETED
    assert resumed_state.result is not None
    assert resumed_state.result["success"] is True


# 19. WindowsDesktopActionController real driver instantiation without exceptions
def test_19_windows_desktop_action_controller_instantiation():
    controller = WindowsDesktopActionController()
    assert hasattr(controller, "focus_window")
    assert hasattr(controller, "set_window_state")
    assert hasattr(controller, "move_resize_window")
