"""Comprehensive test suite for Phase 9 controlled read-only desktop awareness."""
from __future__ import annotations

import asyncio
import json
import pytest
from fastapi.testclient import TestClient

from app.agent.agent import Agent
from app.agent.state import AgentStatus
from app.core.config import get_settings
from app.desktop.inspector import (
    DesktopInspector,
    MockDesktopDriver,
    WindowsDesktopDriver,
    sanitize_window_title,
)
from app.desktop.models import (
    ActiveWindow,
    DesktopWindow,
    DisplayBounds,
    DisplayInfo,
    WindowBounds,
)
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.desktop_active_window import DesktopActiveWindowInput, DesktopActiveWindowTool
from app.tools.desktop_displays import DesktopDisplaysInput, DesktopDisplaysTool
from app.tools.desktop_windows import DesktopWindowsInput, DesktopWindowsTool
from app.tools.executor import Executor, InvalidToolInputError
from app.tools.registry import ToolRegistry
from main import create_app


def make_mock_driver() -> MockDesktopDriver:
    windows = [
        DesktopWindow(
            window_id="win-0x1001",
            title="Project Dashboard - Google Chrome",
            process_id=1024,
            process_name="chrome.exe",
            is_minimized=False,
            bounds=WindowBounds(left=0, top=0, width=1280, height=720),
        ),
        DesktopWindow(
            window_id="win-0x1002",
            title="Terminal - PowerShell",
            process_id=2048,
            process_name="powershell.exe",
            is_minimized=True,
            bounds=WindowBounds(left=100, top=100, width=800, height=600),
        ),
        DesktopWindow(
            window_id="win-0x1003",
            title="Notes - Notepad",
            process_id=3072,
            process_name="notepad.exe",
            is_minimized=False,
            bounds=WindowBounds(left=200, top=200, width=640, height=480),
        ),
    ]
    active_window = ActiveWindow(
        found=True,
        window_id="win-0x1001",
        title="Project Dashboard - Google Chrome",
        process_id=1024,
        process_name="chrome.exe",
        is_minimized=False,
        bounds=WindowBounds(left=0, top=0, width=1280, height=720),
    )
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
            bounds=DisplayBounds(left=1920, top=0, right=4480, bottom=1440, width=2560, height=1440),
            work_area=DisplayBounds(left=1920, top=0, right=4480, bottom=1400, width=2560, height=1400),
            scale_factor=1.25,
        ),
    ]
    return MockDesktopDriver(windows=windows, active_window=active_window, displays=displays)


def make_executor(tmp_path, driver=None) -> Executor:
    repo = SQLiteRepository(tmp_path / "jarvis_desktop_test.sqlite3")
    inspector = DesktopInspector(driver=driver or make_mock_driver())
    registry = ToolRegistry()
    registry.register(DesktopWindowsTool(inspector))
    registry.register(DesktopActiveWindowTool(inspector))
    registry.register(DesktopDisplaysTool(inspector))
    return Executor(registry, repo)


# 1. Window enumeration with mock driver
def test_01_window_enumeration_with_mock_driver():
    driver = make_mock_driver()
    inspector = DesktopInspector(driver=driver)
    res = asyncio.run(inspector.get_windows(limit=50))
    assert res["window_count"] == 3
    windows = res["windows"]
    assert len(windows) == 3
    assert windows[0]["window_id"] == "win-0x1001"
    assert windows[0]["title"] == "Project Dashboard - Google Chrome"
    assert windows[0]["process_name"] == "chrome.exe"
    assert windows[0]["is_minimized"] is False
    assert windows[0]["bounds"]["width"] == 1280
    assert windows[1]["is_minimized"] is True


# 2. Window enumeration limit respected
def test_02_window_enumeration_limit():
    driver = make_mock_driver()
    inspector = DesktopInspector(driver=driver)
    res = asyncio.run(inspector.get_windows(limit=2))
    assert res["window_count"] == 2
    assert len(res["windows"]) == 2


# 3. Active window detection
def test_03_active_window_detection():
    driver = make_mock_driver()
    inspector = DesktopInspector(driver=driver)
    res = asyncio.run(inspector.get_active_window())
    assert res["found"] is True
    assert res["window_id"] == "win-0x1001"
    assert res["title"] == "Project Dashboard - Google Chrome"
    assert res["process_id"] == 1024
    assert res["process_name"] == "chrome.exe"
    assert res["bounds"]["height"] == 720


# 4. Active window not found handled gracefully
def test_04_active_window_not_found():
    driver = MockDesktopDriver(active_window=ActiveWindow(found=False))
    inspector = DesktopInspector(driver=driver)
    res = asyncio.run(inspector.get_active_window())
    assert res["found"] is False
    assert res["window_id"] is None
    assert res["title"] is None


# 5. Display enumeration
def test_05_display_enumeration():
    driver = make_mock_driver()
    inspector = DesktopInspector(driver=driver)
    res = asyncio.run(inspector.get_displays())
    assert res["display_count"] == 2
    assert res["primary_display_index"] == 0
    displays = res["displays"]
    assert displays[0]["id"] == "display-1"
    assert displays[0]["is_primary"] is True
    assert displays[0]["bounds"]["width"] == 1920
    assert displays[0]["work_area"]["height"] == 1040
    assert displays[1]["id"] == "display-2"
    assert displays[1]["is_primary"] is False
    assert displays[1]["bounds"]["width"] == 2560


# 6. Display enumeration empty / fallback handling
def test_06_display_enumeration_empty_driver():
    driver = MockDesktopDriver(displays=[])
    inspector = DesktopInspector(driver=driver)
    res = asyncio.run(inspector.get_displays())
    assert res["display_count"] == 0
    assert res["primary_display_index"] == 0
    assert res["displays"] == []


# 7. Title sanitizer redacts Windows user paths
def test_07_title_sanitizer_redacts_windows_user_paths():
    assert (
        sanitize_window_title(r"C:\Users\JohnDoe\Documents\report.docx - Word")
        == r"C:\Users\[REDACTED]\Documents\report.docx - Word"
    )
    assert (
        sanitize_window_title("C:/Users/JaneDoe/file.txt - Notepad")
        == "C:/Users/[REDACTED]/file.txt - Notepad"
    )


# 8. Title sanitizer redacts Unix user paths
def test_08_title_sanitizer_redacts_unix_user_paths():
    assert (
        sanitize_window_title("/home/alice/project/main.py - Visual Studio Code")
        == "/home/[REDACTED]/project/main.py - Visual Studio Code"
    )
    assert (
        sanitize_window_title("/Users/bob/Downloads/file.pdf - Preview")
        == "/Users/[REDACTED]/Downloads/file.pdf - Preview"
    )


# 9. Title sanitizer redacts sensitive files
def test_09_title_sanitizer_redacts_sensitive_files():
    assert sanitize_window_title(".env - Visual Studio Code") == "[REDACTED_FILE] - Visual Studio Code"
    assert sanitize_window_title(".env.production - Visual Studio Code") == "[REDACTED_FILE] - Visual Studio Code"
    assert sanitize_window_title("id_rsa - Notepad") == "[REDACTED_FILE] - Notepad"
    assert sanitize_window_title("id_ed25519.pub - Editor") == "[REDACTED_FILE] - Editor"
    assert sanitize_window_title("credentials.json - Sublime") == "[REDACTED_FILE] - Sublime"
    assert sanitize_window_title("secrets.yaml - Sublime") == "[REDACTED_FILE] - Sublime"
    assert sanitize_window_title("private_server.key - Notepad++") == "[REDACTED_FILE] - Notepad++"
    assert sanitize_window_title("cert.pem - TextEdit") == "[REDACTED_FILE] - TextEdit"


# 10. Title sanitizer redacts credentials, tokens, API keys, and JWTs
def test_10_title_sanitizer_redacts_credentials():
    assert sanitize_window_title("Settings: password=mysecret123 - Chrome") == "Settings: [REDACTED] - Chrome"
    assert sanitize_window_title("Config: token=secret-token-val - Chrome") == "Config: [REDACTED] - Chrome"
    assert sanitize_window_title("API Key: api_key=abc123456789 - App") == "API Key: [REDACTED] - App"
    assert (
        sanitize_window_title("Token: Bearer mysecretbearerauthtoken123 - App")
        == "Token: Bearer [REDACTED] - App"
    )
    assert (
        sanitize_window_title("Auth: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignatureNow - App")
        == "Auth: [REDACTED_JWT] - App"
    )
    assert sanitize_window_title("Key: sk-ant-api03-abcdefghijklmnopqrstu - Console") == "Key: [REDACTED_KEY] - Console"
    assert sanitize_window_title("Card: 4111 2222 3333 4444 - Billing") == "Card: [REDACTED_CARD] - Billing"


# 11. Title sanitizer handles edge cases
def test_11_title_sanitizer_edge_cases():
    assert sanitize_window_title(None) == "Untitled"
    assert sanitize_window_title("") == "Untitled"
    assert sanitize_window_title("   ") == "Untitled"
    very_long = "A" * 200
    sanitized = sanitize_window_title(very_long)
    assert len(sanitized) <= 120


# 12. Tools registered in main application
def test_12_tools_registered_in_app():
    app = create_app()
    registry: ToolRegistry = app.state.executor.registry
    tool_names = [t.name for t in registry.list_tools()]
    assert "desktop_windows" in tool_names
    assert "desktop_active_window" in tool_names
    assert "desktop_displays" in tool_names


# 13. SAFE permission level is strictly enforced
def test_13_safe_permission_level_enforced():
    tools = [
        DesktopWindowsTool(),
        DesktopActiveWindowTool(),
        DesktopDisplaysTool(),
    ]
    for tool in tools:
        assert tool.permission_level is PermissionLevel.SAFE


# 14. Desktop tools, driver, and inspector have NO mutating action methods
def test_14_absence_of_desktop_action_methods():
    tools = [
        DesktopWindowsTool(),
        DesktopActiveWindowTool(),
        DesktopDisplaysTool(),
    ]
    forbidden_methods = [
        "click", "focus", "activate", "minimize", "maximize",
        "move", "resize", "close", "press_key", "type", "send_keys",
        "screenshot", "capture_screen", "ocr", "kill_process",
        "launch_process", "execute_shell",
    ]
    for tool in tools:
        for method in forbidden_methods:
            assert not hasattr(tool, method), f"{tool.name} must not have action method '{method}'"

    inspector = DesktopInspector()
    for method in forbidden_methods:
        assert not hasattr(inspector, method), f"DesktopInspector must not have action method '{method}'"

    driver = WindowsDesktopDriver()
    for method in forbidden_methods:
        assert not hasattr(driver, method), f"WindowsDesktopDriver must not have action method '{method}'"


# 15. Desktop tools execute through Executor with full audit trail
def test_15_desktop_tools_execute_through_executor_with_audit(tmp_path):
    driver = make_mock_driver()
    executor = make_executor(tmp_path, driver=driver)

    res_win = asyncio.run(executor.execute("desktop_windows", {"limit": 10}))
    assert res_win["window_count"] == 3

    res_act = asyncio.run(executor.execute("desktop_active_window", {}))
    assert res_act["found"] is True

    res_disp = asyncio.run(executor.execute("desktop_displays", {}))
    assert res_disp["display_count"] == 2

    events = executor.audit_events
    tool_names = {e.tool for e in events}
    assert "desktop_windows" in tool_names
    assert "desktop_active_window" in tool_names
    assert "desktop_displays" in tool_names

    win_events = [e for e in events if e.tool == "desktop_windows"]
    win_event_types = [e.event_type for e in win_events]
    assert "tool_requested" in win_event_types
    assert "validation_succeeded" in win_event_types
    assert "permission_decision" in win_event_types
    assert "execution_started" in win_event_types
    assert "execution_succeeded" in win_event_types


# 16. Desktop windows input validation bounds
def test_16_desktop_windows_input_validation(tmp_path):
    executor = make_executor(tmp_path)
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("desktop_windows", {"limit": 0}))

    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("desktop_windows", {"limit": -5}))

    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("desktop_windows", {"limit": 101}))

    # Valid limits
    res_min = asyncio.run(executor.execute("desktop_windows", {"limit": 1}))
    assert res_min["window_count"] == 1
    res_max = asyncio.run(executor.execute("desktop_windows", {"limit": 100}))
    assert res_max["window_count"] == 3


# 17. API authentication and RBAC enforcement
def test_17_api_rbac_enforcement(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(tmp_path / "jarvis.sqlite3"))
    monkeypatch.setenv(
        "JARVIS_AUTH_TOKENS",
        json.dumps([
            {"subject": "owner", "role": "owner", "token": "test-owner-token-desktop"},
            {"subject": "auditor", "role": "auditor", "token": "test-auditor-token-desktop"},
        ]),
    )
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    # Missing auth -> 401
    assert client.post("/actions/desktop_windows/execute", json={"input": {}}).status_code == 401
    assert client.post("/actions/desktop_active_window/execute", json={"input": {}}).status_code == 401
    assert client.post("/actions/desktop_displays/execute", json={"input": {}}).status_code == 401

    # Auditor -> 403
    auditor_headers = {"Authorization": "Bearer test-auditor-token-desktop"}
    assert client.post("/actions/desktop_windows/execute", headers=auditor_headers, json={"input": {}}).status_code == 403

    # Owner -> 200
    owner_headers = {"Authorization": "Bearer test-owner-token-desktop"}
    res = client.post("/actions/desktop_windows/execute", headers=owner_headers, json={"input": {}})
    assert res.status_code == 200
    assert "window_count" in res.json()


# 18. Agent executes desktop awareness tools end-to-end
def test_18_agent_executes_desktop_tools_end_to_end(tmp_path):
    driver = make_mock_driver()
    executor = make_executor(tmp_path, driver=driver)
    agent = Agent(executor=executor)

    for tool_name in ("desktop_windows", "desktop_active_window", "desktop_displays"):
        state = agent.prepare(f"inspect {tool_name}")
        updated_state = asyncio.run(agent.execute_step(state, tool_name, {}))
        assert updated_state.status is AgentStatus.COMPLETED
        assert updated_state.result is not None
        assert updated_state.error is None

        # Verify persisted in SQLite
        loaded_state = agent.get_session(state.session_id)
        assert loaded_state.status is AgentStatus.COMPLETED
        assert loaded_state.result == updated_state.result


# 19. WindowsDesktopDriver real execution on host runs without throwing
def test_19_windows_desktop_driver_runs_without_exceptions():
    driver = WindowsDesktopDriver()
    windows = driver.get_windows(limit=10)
    assert isinstance(windows, list)

    active = driver.get_active_window()
    assert isinstance(active, ActiveWindow)

    displays = driver.get_displays()
    assert isinstance(displays, list)
    # On Windows, at least one display or fallback display is detected
    assert len(displays) >= 1
    assert displays[0].bounds.width > 0
    assert displays[0].bounds.height > 0
