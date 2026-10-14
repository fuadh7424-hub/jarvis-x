"""Comprehensive test suite for Phase 8 controlled browser actions."""
import asyncio
import json
import pytest
from pydantic import ValidationError

from app.agent.agent import Agent
from app.agent.state import AgentStatus
from app.browser.actions import (
    BrowserActionController,
    BrowserDriver,
    ElementTarget,
    validate_navigation_url,
    validate_non_sensitive_text,
)
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.browser_click import BrowserClickInput, BrowserClickTool
from app.tools.browser_navigate import BrowserNavigateInput, BrowserNavigateTool
from app.tools.browser_new_tab import BrowserNewTabInput, BrowserNewTabTool
from app.tools.browser_type import BrowserTypeInput, BrowserTypeTool
from app.tools.executor import (
    ApprovalRejectedError,
    ApprovalRequiredError,
    Executor,
    InvalidToolInputError,
)
from app.tools.registry import ToolRegistry
from main import create_app


class MockActionDriver(BrowserDriver):
    """Hermetic mock driver for testing browser actions and edge cases."""

    def __init__(
        self,
        navigate_result: tuple[bool, bool, str | None, str | None] = (True, True, "https://www.youtube.com", None),
        click_result: tuple[bool, bool, str | None, str | None] = (True, True, "https://current.page", None),
        type_result: tuple[bool, bool, str | None, str | None] = (True, True, "https://current.page", None),
        new_tab_result: tuple[bool, bool, str | None, str | None, str | None] = (True, True, "tab-mock-1", "https://newtab.url", None),
    ) -> None:
        self.navigate_result = navigate_result
        self.click_result = click_result
        self.type_result = type_result
        self.new_tab_result = new_tab_result
        self.calls: list[dict[str, str | int | None]] = []

    async def navigate(self, url: str, port: int):
        self.calls.append({"action": "navigate", "url": url, "port": port})
        return self.navigate_result

    async def click(self, target: ElementTarget, port: int):
        self.calls.append({"action": "click", "target": target.representation(), "port": port})
        return self.click_result

    async def type_text(self, target: ElementTarget, text: str, port: int):
        self.calls.append({"action": "type_text", "target": target.representation(), "port": port})
        return self.type_result

    async def new_tab(self, url: str | None, port: int):
        self.calls.append({"action": "new_tab", "url": url, "port": port})
        return self.new_tab_result


def make_executor(tmp_path, driver: BrowserDriver | None = None) -> Executor:
    controller = BrowserActionController(driver=driver or MockActionDriver())
    registry = ToolRegistry()
    registry.register(BrowserNavigateTool(controller))
    registry.register(BrowserClickTool(controller))
    registry.register(BrowserTypeTool(controller))
    registry.register(BrowserNewTabTool(controller))
    repo = SQLiteRepository(tmp_path / "jarvis.sqlite3")
    return Executor(registry, repo, audit_sink=lambda _: None)


# 1, 2, 3, 4. All mutating browser actions require PermissionLevel.CONFIRM
def test_1_to_4_mutating_actions_require_confirm():
    assert BrowserNavigateTool().permission_level is PermissionLevel.CONFIRM
    assert BrowserClickTool().permission_level is PermissionLevel.CONFIRM
    assert BrowserTypeTool().permission_level is PermissionLevel.CONFIRM
    assert BrowserNewTabTool().permission_level is PermissionLevel.CONFIRM


# 5 & 6. Missing approval blocks mutating action execution with ApprovalRequiredError
def test_5_and_6_missing_approval_blocks_execution(tmp_path):
    executor = make_executor(tmp_path)
    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("browser_navigate", {"url": "https://www.google.com"}))

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("browser_click", {"target_selector": "button.submit"}))

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("browser_type", {"text": "hello", "target_id": "search"}))

    with pytest.raises(ApprovalRequiredError):
        asyncio.run(executor.execute("browser_new_tab", {}))

    # Audit log confirms approval_required was recorded
    events = executor.audit_events
    assert any(e.event_type == "approval_required" for e in events)


# 7. Denied approval blocks action execution
def test_7_denied_approval_blocks_action(tmp_path):
    executor = make_executor(tmp_path)
    # Create approval request
    req = executor.create_approval(
        "browser_navigate",
        {"url": "https://www.youtube.com"},
        reason="Navigate to YouTube",
        requested_by="user",
    )
    # Deny approval
    executor.resolve_approval(req.id, approved=False)

    # Attempting to execute with rejected approval raises ApprovalRejectedError
    with pytest.raises(ApprovalRejectedError):
        asyncio.run(executor.execute("browser_navigate", {"url": "https://www.youtube.com"}, approval_id=req.id))


# 8. Approved action reaches executor and consumes approval token atomically
def test_8_approved_action_reaches_executor_and_consumes_approval(tmp_path):
    driver = MockActionDriver(navigate_result=(True, True, "https://www.youtube.com", None))
    executor = make_executor(tmp_path, driver=driver)

    req = executor.create_approval(
        "browser_navigate",
        {"url": "https://www.youtube.com"},
        reason="Navigate to YouTube",
        requested_by="user",
    )
    executor.resolve_approval(req.id, approved=True)

    res = asyncio.run(executor.execute("browser_navigate", {"url": "https://www.youtube.com"}, approval_id=req.id))
    assert res["success"] is True
    assert res["verified"] is True
    assert res["url"] == "https://www.youtube.com"

    # Approval cannot be reused (one-time consumption)
    with pytest.raises(Exception):
        asyncio.run(executor.execute("browser_navigate", {"url": "https://www.youtube.com"}, approval_id=req.id))


# 9, 10, 11, 12. Invalid schemes rejected (no scheme, javascript, data, file)
def test_9_to_12_url_schemes_validation():
    # Valid http and https
    assert "https://example.com" in validate_navigation_url("https://example.com")
    assert "http://localhost:8080" in validate_navigation_url("http://localhost:8080")

    # Invalid / forbidden schemes
    with pytest.raises(ValueError, match="scheme"):
        validate_navigation_url("example.com")  # missing scheme

    with pytest.raises(ValueError, match="Forbidden or unsupported"):
        validate_navigation_url("javascript:alert(1)")

    with pytest.raises(ValueError, match="Forbidden or unsupported"):
        validate_navigation_url("data:text/html,<h1>bad</h1>")

    with pytest.raises(ValueError, match="Forbidden or unsupported"):
        validate_navigation_url("file:///C:/Windows/win.ini")

    with pytest.raises(ValueError, match="Forbidden or unsupported"):
        validate_navigation_url("vbscript:msgbox(1)")

    with pytest.raises(ValueError, match="Forbidden or unsupported"):
        validate_navigation_url("chrome://settings")


# 13. Embedded credentials stripped and sensitive query params redacted
def test_13_embedded_credentials_and_tokens_sanitized():
    dirty = "https://user:secret_pass@example.com/login?token=xyz123&session_id=abc&query=normal"
    clean = validate_navigation_url(dirty)
    assert "secret_pass" not in clean
    assert "user@" not in clean
    assert "xyz123" not in clean
    assert "abc" not in clean
    assert "token=%5BREDACTED%5D" in clean or "token=[REDACTED]" in clean
    assert "query=normal" in clean


# 14. Sensitive typing rejected (passwords, API keys, tokens)
def test_14_sensitive_typing_rejected():
    target = ElementTarget(element_id="searchbox")

    # Passwords blocked
    with pytest.raises(ValueError, match="(?i)sensitive"):
        validate_non_sensitive_text("my_secret_password_123", target)

    # API keys blocked
    with pytest.raises(ValueError, match="(?i)sensitive"):
        validate_non_sensitive_text("sk_live_1234567890abcdefghijklm", target)

    # Bearer tokens blocked
    with pytest.raises(ValueError, match="(?i)sensitive"):
        validate_non_sensitive_text("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", target)

    # Typing into password fields blocked even with benign text
    pass_target = ElementTarget(element_id="user_password_input")
    with pytest.raises(ValueError, match="credential fields is blocked"):
        validate_non_sensitive_text("regular_text", pass_target)


# 15. Sensitive input is not written to audit logs (raw text omitted)
def test_15_raw_text_omitted_from_audit_and_metadata(tmp_path):
    driver = MockActionDriver()
    executor = make_executor(tmp_path, driver=driver)

    req = executor.create_approval(
        "browser_type",
        {"text": "search query for weather", "target_id": "search_input"},
        reason="Search weather",
        requested_by="user",
    )
    executor.resolve_approval(req.id, approved=True)

    res = asyncio.run(executor.execute(
        "browser_type",
        {"text": "search query for weather", "target_id": "search_input"},
        approval_id=req.id,
    ))
    assert res["success"] is True
    # Verify metadata contains text_length rather than raw text
    assert res["metadata"]["text_length"] == len("search query for weather")
    assert "search query for weather" not in res.get("target", "")

    # Check audit events
    audit_events_json = json.dumps([e.model_dump(mode="json") for e in executor.audit_events])
    assert "search query for weather" not in audit_events_json


# 16. Browser disconnect fails safely
def test_16_browser_disconnect_fails_safely():
    driver = MockActionDriver(navigate_result=(False, False, None, "BROWSER_DISCONNECTED"))
    controller = BrowserActionController(driver=driver)
    res = asyncio.run(controller.navigate("https://example.com"))
    assert res.success is False
    assert res.verified is False
    assert res.error == "BROWSER_DISCONNECTED"


# 17 & 18. Stale or ambiguous targets fail safely
def test_17_and_18_stale_or_ambiguous_targets_fail_safely():
    # Ambiguous target simulation
    ambig_driver = MockActionDriver(click_result=(False, False, None, "AMBIGUOUS_TARGET"))
    controller_ambig = BrowserActionController(driver=ambig_driver)
    res_ambig = asyncio.run(controller_ambig.click(ElementTarget(selector=".btn")))
    assert res_ambig.success is False
    assert res_ambig.error == "AMBIGUOUS_TARGET"

    # Stale target simulation
    stale_driver = MockActionDriver(click_result=(False, False, None, "STALE_TARGET"))
    controller_stale = BrowserActionController(driver=stale_driver)
    res_stale = asyncio.run(controller_stale.click(ElementTarget(element_id="stale_btn")))
    assert res_stale.success is False
    assert res_stale.error == "STALE_TARGET"

    # Missing target specification
    empty_target = ElementTarget()
    res_empty = asyncio.run(controller_stale.click(empty_target))
    assert res_empty.success is False
    assert res_empty.error == "TARGET_NOT_FOUND"


# 19. Successful navigation is verified
def test_19_successful_navigation_verified():
    driver = MockActionDriver(navigate_result=(True, True, "https://www.youtube.com", None))
    controller = BrowserActionController(driver=driver)
    res = asyncio.run(controller.navigate("https://www.youtube.com"))
    assert res.success is True
    assert res.verified is True
    assert res.url == "https://www.youtube.com"


# 20. Failed verification is reported correctly without claiming false success
def test_20_failed_verification_reported_correctly():
    # Navigation succeeded but target page could not be verified (mismatch or redirect)
    driver = MockActionDriver(navigate_result=(True, False, "https://blocked.destination", "VERIFICATION_FAILED"))
    controller = BrowserActionController(driver=driver)
    res = asyncio.run(controller.navigate("https://www.youtube.com"))
    assert res.verified is False


# 21. Every executed action creates complete audit trail
def test_21_every_action_creates_audit_events(tmp_path):
    driver = MockActionDriver()
    executor = make_executor(tmp_path, driver=driver)

    req = executor.create_approval(
        "browser_new_tab",
        {"url": "https://www.wikipedia.org"},
        reason="Open Wikipedia",
        requested_by="user",
    )
    executor.resolve_approval(req.id, approved=True)

    asyncio.run(executor.execute("browser_new_tab", {"url": "https://www.wikipedia.org"}, approval_id=req.id))

    event_types = [e.event_type for e in executor.audit_events if e.tool == "browser_new_tab"]
    assert "tool_requested" in event_types
    assert "validation_succeeded" in event_types
    assert "permission_decision" in event_types
    assert "approval_created" in event_types
    assert "approval_consumed" in event_types
    assert "execution_started" in event_types
    assert "execution_succeeded" in event_types


# 22. No browser action bypasses existing execution pipeline (Agent end-to-end integration)
def test_22_agent_state_machine_pipeline_integration(tmp_path):
    driver = MockActionDriver(navigate_result=(True, True, "https://www.youtube.com", None))
    executor = make_executor(tmp_path, driver=driver)
    agent = Agent(executor=executor)

    # 1. Prepare agent goal
    state = agent.prepare("Open YouTube")
    assert state.status is AgentStatus.PLANNING

    # 2. Execute step requires approval for CONFIRM-level tool
    state = asyncio.run(agent.execute_step(state, "browser_navigate", {"url": "https://www.youtube.com"}))
    assert state.status is AgentStatus.WAITING_FOR_APPROVAL
    assert state.approval_id is not None

    # Check persistence in SQLite
    saved_state = agent.get_session(state.session_id)
    assert saved_state.status is AgentStatus.WAITING_FOR_APPROVAL

    # 3. User approves
    executor.resolve_approval(state.approval_id, approved=True)

    # 4. Agent resumes and executes step
    res_state = asyncio.run(agent.resume(state, {"url": "https://www.youtube.com"}))
    assert res_state.status is AgentStatus.COMPLETED
    assert res_state.result["success"] is True
    assert res_state.result["verified"] is True

    # 5. Verify final session persistence in SQLite
    final_saved = agent.get_session(state.session_id)
    assert final_saved.status is AgentStatus.COMPLETED
    assert final_saved.result["url"] == "https://www.youtube.com"


# 23. Tools registered in main application registry
def test_23_phase8_tools_registered_in_app():
    app = create_app()
    registry: ToolRegistry = app.state.executor.registry
    tool_names = [t.name for t in registry.list_tools()]
    assert "browser_navigate" in tool_names
    assert "browser_click" in tool_names
    assert "browser_type" in tool_names
    assert "browser_new_tab" in tool_names
