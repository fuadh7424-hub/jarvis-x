"""Comprehensive test suite for Phase 7 controlled read-only browser awareness."""
import asyncio
import json
import pytest
import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.agent import Agent
from app.agent.state import AgentStatus
from app.browser.inspector import BrowserInspector
from app.browser.models import BrowserStatus, BrowserTab, PageMetadata
from app.core.config import get_settings
from app.security.access import AuthorizationContext
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.browser_active_tab import ActiveTabInput, ActiveTabTool
from app.tools.browser_page_metadata import PageMetadataInput, PageMetadataTool
from app.tools.browser_status import BrowserStatusInput, BrowserStatusTool
from app.tools.browser_tabs import BrowserTabsInput, BrowserTabsTool
from app.tools.executor import Executor, InvalidToolInputError
from app.tools.registry import ToolRegistry
from main import create_app


def make_mock_client(version_data=None, list_data=None, status_code=200, malformed=False):
    """Create an in-memory mock httpx.AsyncClient simulating CDP endpoints."""
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if malformed:
            return httpx.Response(200, content=b"<html>Not JSON</html>", headers={"content-type": "text/html"})
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "server_error"})
        if "version" in url_str:
            return httpx.Response(
                200,
                json=version_data
                if version_data is not None
                else {
                    "Browser": "Chrome/131.0.6778.86",
                    "Protocol-Version": "1.3",
                    "User-Agent": "Mozilla/5.0",
                },
            )
        if "list" in url_str:
            return httpx.Response(
                200,
                json=list_data
                if list_data is not None
                else [
                    {
                        "id": "tab-1",
                        "title": "GitHub: Let's build from here",
                        "url": "https://github.com/fuadh7424-hub/jarvis-x",
                        "type": "page",
                    },
                    {
                        "id": "tab-2",
                        "title": "FastAPI Documentation",
                        "url": "https://fastapi.tiangolo.com/",
                        "type": "page",
                    },
                ],
            )
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_executor(tmp_path, inspector: BrowserInspector | None = None) -> Executor:
    registry = ToolRegistry()
    registry.register(BrowserStatusTool(inspector))
    registry.register(BrowserTabsTool(inspector))
    registry.register(ActiveTabTool(inspector))
    registry.register(PageMetadataTool(inspector))
    repo = SQLiteRepository(tmp_path / "jarvis.sqlite3")
    return Executor(registry, repo, audit_sink=lambda _: None)


# 1. Browser status tool structure & availability
def test_1_browser_status_structure():
    tool = BrowserStatusTool()
    res = asyncio.run(tool.execute(BrowserStatusInput(port=9222)))
    assert "supported_browsers" in res
    assert "detected_browsers" in res
    assert "is_running" in res
    assert "connected" in res
    assert "message" in res
    assert isinstance(res["supported_browsers"], list)
    assert isinstance(res["detected_browsers"], list)


# 2. Browser status with connected mock CDP
def test_2_browser_status_connected_cdp():
    client = make_mock_client()
    inspector = BrowserInspector(client=client)
    tool = BrowserStatusTool(inspector=inspector)
    res = asyncio.run(tool.execute(BrowserStatusInput()))
    assert res["connected"] is True
    assert res["browser_name"] == "Chrome/131.0.6778.86"
    assert res["browser_version"] == "1.3"
    assert "inspection_endpoint" in res


# 3. Tab metadata returns safe structured data
def test_3_browser_tabs_metadata():
    client = make_mock_client()
    inspector = BrowserInspector(client=client)
    tool = BrowserTabsTool(inspector=inspector)
    res = asyncio.run(tool.execute(BrowserTabsInput(limit=10)))
    assert res["total_count"] == 2
    assert res["returned_count"] == 2
    assert res["source"] == "cdp"
    assert len(res["tabs"]) == 2

    first = res["tabs"][0]
    assert first["id"] == "tab-1"
    assert "GitHub" in first["title"]
    assert first["url"] == "https://github.com/fuadh7424-hub/jarvis-x"
    assert first["is_active"] is True
    assert first["browser"] == "chrome"


# 4. Active tab returns URL and title only
def test_4_active_tab_metadata():
    client = make_mock_client()
    inspector = BrowserInspector(client=client)
    tool = ActiveTabTool(inspector=inspector)
    res = asyncio.run(tool.execute(ActiveTabInput()))
    assert res["found"] is True
    assert res["id"] == "tab-1"
    assert "GitHub" in res["title"]
    assert res["url"] == "https://github.com/fuadh7424-hub/jarvis-x"
    assert res["browser"] == "chrome"
    assert res["source"] == "cdp"


# 5. Page metadata returns safe high-level metadata
def test_5_page_metadata():
    client = make_mock_client()
    inspector = BrowserInspector(client=client)
    tool = PageMetadataTool(inspector=inspector)
    res = asyncio.run(tool.execute(PageMetadataInput()))
    assert res["found"] is True
    assert "GitHub" in res["title"]
    assert res["domain"] == "github.com"
    assert res["protocol"] == "https"
    assert res["is_secure"] is True
    assert res["source"] == "cdp"


# 6. Safe URL sanitization: embedded credentials and sensitive tokens stripped
def test_6_safe_url_and_title_handling():
    dirty_url = "https://admin:super_secret_password@example.com/api?access_token=secret_token_123&public_param=allowed"
    clean_url = BrowserInspector.sanitize_url(dirty_url)
    assert clean_url is not None
    assert "super_secret_password" not in clean_url
    assert "admin@" not in clean_url
    assert "secret_token_123" not in clean_url
    assert "access_token=%5BREDACTED%5D" in clean_url or "access_token=[REDACTED]" in clean_url
    assert "public_param=allowed" in clean_url

    # Long title bounded
    long_title = "A" * 200
    clean_title = BrowserInspector.sanitize_title(long_title)
    assert len(clean_title) <= 120

    # Empty title handled
    assert BrowserInspector.sanitize_title(None) == "Untitled"
    assert BrowserInspector.sanitize_title("   ") == "Untitled"


# 7. Empty browser state (no open tabs)
def test_7_empty_browser_state():
    client = make_mock_client(list_data=[])
    inspector = BrowserInspector(client=client)

    tabs_tool = BrowserTabsTool(inspector=inspector)
    tabs_res = asyncio.run(tabs_tool.execute(BrowserTabsInput()))
    assert tabs_res["total_count"] == 0
    assert tabs_res["tabs"] == []

    active_tool = ActiveTabTool(inspector=inspector)
    active_res = asyncio.run(active_tool.execute(ActiveTabInput()))
    assert active_res["found"] is False
    assert active_res["title"] is None
    assert active_res["url"] is None


# 8. Unavailable/disconnected browser handling
def test_8_unavailable_browser_handling():
    # Transport that refuses connection
    def conn_refused(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(conn_refused))
    inspector = BrowserInspector(client=client)

    tool = BrowserStatusTool(inspector=inspector)
    res = asyncio.run(tool.execute(BrowserStatusInput()))
    assert res["connected"] is False
    assert res["inspection_endpoint"] is None


# 9. Malformed browser state / non-JSON responses
def test_9_malformed_browser_state():
    client = make_mock_client(malformed=True)
    inspector = BrowserInspector(client=client)

    tool = BrowserTabsTool(inspector=inspector)
    # Must fail safely without crashing
    res = asyncio.run(tool.execute(BrowserTabsInput()))
    assert isinstance(res, dict)
    assert "tabs" in res


# 10. Sensitive data exclusion: cookies, tokens, storage, passwords never returned
def test_10_sensitive_data_exclusion():
    client = make_mock_client()
    inspector = BrowserInspector(client=client)
    tools = [
        BrowserStatusTool(inspector),
        BrowserTabsTool(inspector),
        ActiveTabTool(inspector),
        PageMetadataTool(inspector),
    ]

    for tool in tools:
        input_model = tool.input_model()
        data = asyncio.run(tool.execute(input_model))
        serialized = json.dumps(data)

        for forbidden in ("cookies", "cookie", "localStorage", "sessionStorage", "password", "token", "auth_header"):
            assert forbidden not in data, f"{tool.name} must not contain '{forbidden}' field"


# 11. Security invariants: no execution, typing, clicking, or navigation capabilities
def test_11_browser_tools_have_no_action_methods():
    tools = [
        BrowserStatusTool(),
        BrowserTabsTool(),
        ActiveTabTool(),
        PageMetadataTool(),
    ]
    forbidden_methods = [
        "click", "type", "navigate", "goto", "eval", "evaluate",
        "execute_script", "close", "new_page", "download", "upload",
        "screenshot", "press", "fill", "submit"
    ]
    for tool in tools:
        for method in forbidden_methods:
            assert not hasattr(tool, method), f"{tool.name} must not have action method '{method}'"


# 12. Tools registered correctly in application
def test_12_tools_registered_in_app():
    app = create_app()
    registry: ToolRegistry = app.state.executor.registry
    tool_names = [t.name for t in registry.list_tools()]
    assert "browser_status" in tool_names
    assert "browser_tabs" in tool_names
    assert "active_tab" in tool_names
    assert "page_metadata" in tool_names


# 13. SAFE permission level is enforced
def test_13_safe_permission_level_enforced():
    tools = [
        BrowserStatusTool(),
        BrowserTabsTool(),
        ActiveTabTool(),
        PageMetadataTool(),
    ]
    for tool in tools:
        assert tool.permission_level is PermissionLevel.SAFE


# 14. SAFE browser tools pass through Executor with audit events
def test_14_safe_browser_tools_pass_through_executor_with_audit(tmp_path):
    client = make_mock_client()
    inspector = BrowserInspector(client=client)
    executor = make_executor(tmp_path, inspector=inspector)

    res = asyncio.run(executor.execute("browser_status", {"port": 9222}))
    assert res["connected"] is True

    events = executor.audit_events
    tool_events = [e for e in events if e.tool == "browser_status"]
    event_types = [e.event_type for e in tool_events]
    assert "tool_requested" in event_types
    assert "validation_succeeded" in event_types
    assert "permission_decision" in event_types
    assert "execution_started" in event_types
    assert "execution_succeeded" in event_types


# 15. Tool input validation bounds
def test_15_tool_input_validation(tmp_path):
    executor = make_executor(tmp_path)
    # Port out of bounds
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("browser_status", {"port": 99}))

    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("browser_status", {"port": 70000}))

    # Tabs limit out of bounds
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("browser_tabs", {"limit": 200}))


# 16. Unauthorized callers cannot invoke browser tools via API
def test_16_unauthorized_callers_cannot_invoke_browser_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(tmp_path / "jarvis.sqlite3"))
    monkeypatch.setenv(
        "JARVIS_AUTH_TOKENS",
        json.dumps([
            {"subject": "owner", "role": "owner", "token": "test-owner-token-phase7"},
            {"subject": "auditor", "role": "auditor", "token": "test-auditor-token-phase7"},
        ]),
    )
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    # Missing auth -> 401
    assert client.post("/actions/browser_status/execute", json={"input": {}}).status_code == 401

    # Auditor -> 403
    auditor_headers = {"Authorization": "Bearer test-auditor-token-phase7"}
    assert client.post("/actions/browser_status/execute", headers=auditor_headers, json={"input": {}}).status_code == 403

    # Owner -> 200
    owner_headers = {"Authorization": "Bearer test-owner-token-phase7"}
    res = client.post("/actions/browser_status/execute", headers=owner_headers, json={"input": {}})
    assert res.status_code == 200
    assert "supported_browsers" in res.json()


# 17. Agent executes browser tools end-to-end through state machine
def test_17_agent_executes_browser_tools_end_to_end(tmp_path):
    client = make_mock_client()
    inspector = BrowserInspector(client=client)
    executor = make_executor(tmp_path, inspector=inspector)
    agent = Agent(executor=executor)

    for tool_name in ("browser_status", "browser_tabs", "active_tab", "page_metadata"):
        state = agent.prepare(f"inspect {tool_name}")
        res = asyncio.run(agent.execute_step(state, tool_name, {}))
        assert res.status is AgentStatus.COMPLETED
        assert res.result is not None
        assert res.error is None

        # Verify durable SQLite persistence
        saved = agent.get_session(state.session_id)
        assert saved.status is AgentStatus.COMPLETED
        assert saved.result == res.result
