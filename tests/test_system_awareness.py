import asyncio
import json
import os
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.agent import Agent
from app.agent.state import AgentStatus
from app.core.config import get_settings
from app.security.access import AuthorizationContext
from app.security.permissions import PermissionLevel
from app.storage.sqlite import SQLiteRepository
from app.tools.application_list import ApplicationListInput, ApplicationListTool
from app.tools.executor import Executor, InvalidToolInputError
from app.tools.process_list import ProcessListInput, ProcessListTool
from app.tools.registry import ToolRegistry
from app.tools.resource_metrics import ResourceMetricsInput, ResourceMetricsTool
from app.tools.system_info import SystemInfoInput, SystemInfoTool
from app.tools.system_time import SystemTimeInput, SystemTimeTool
from main import create_app


def make_executor(tmp_path) -> Executor:
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    registry.register(SystemTimeTool())
    registry.register(ResourceMetricsTool())
    registry.register(ProcessListTool())
    registry.register(ApplicationListTool())
    repo = SQLiteRepository(tmp_path / "jarvis.sqlite3")
    return Executor(registry, repo, audit_sink=lambda _: None)


# 1. System information tool returns valid structured data
def test_1_system_info_tool_structure():
    tool = SystemInfoTool()
    data = asyncio.run(tool.execute(SystemInfoInput(include_resources=True)))
    assert isinstance(data["os_name"], str)
    assert isinstance(data["os_version"], str)
    assert isinstance(data["machine"], str)
    assert isinstance(data["architecture"], str)
    assert isinstance(data["hostname"], str)
    assert isinstance(data["python_version"], str)
    assert "cpu_count" in data

    # Test without resource count
    data_no_res = asyncio.run(tool.execute(SystemInfoInput(include_resources=False)))
    assert "cpu_count" not in data_no_res
    assert isinstance(data_no_res["os_name"], str)


# 2. Time tool returns valid structured data
def test_2_system_time_tool_structure():
    tool = SystemTimeTool()
    data = asyncio.run(tool.execute(SystemTimeInput(include_epoch=True)))
    assert "local_time" in data
    assert "utc_time" in data
    assert "timezone_name" in data
    assert "utc_offset" in data
    assert "day_of_week" in data
    assert "timestamp_epoch" in data
    assert isinstance(data["is_dst"], bool)

    # Test without epoch
    data_no_epoch = asyncio.run(tool.execute(SystemTimeInput(include_epoch=False)))
    assert "timestamp_epoch" not in data_no_epoch
    assert "local_time" in data_no_epoch


# 3. Resource metrics tool returns valid structured data
def test_3_resource_metrics_tool_structure():
    tool = ResourceMetricsTool()
    data = asyncio.run(tool.execute(ResourceMetricsInput(disk_path="C:\\")))
    assert "cpu" in data
    assert "logical_cpu_count" in data["cpu"]
    assert "cpu_usage_percent" in data["cpu"]
    assert "memory" in data
    assert "total_mb" in data["memory"]
    assert "available_mb" in data["memory"]
    assert "percent_used" in data["memory"]
    assert "disk" in data
    assert data["disk"]["path"] == "C:\\"
    assert "total_gb" in data["disk"]
    assert "free_gb" in data["disk"]


# 4. Process inspection returns safe process metadata
def test_4_process_list_returns_safe_metadata():
    tool = ProcessListTool()
    data = asyncio.run(tool.execute(ProcessListInput(limit=10, sort_by="memory")))
    assert "total_matched" in data
    assert "returned_count" in data
    assert "processes" in data
    assert len(data["processes"]) <= 10
    if data["processes"]:
        p = data["processes"][0]
        assert "pid" in p
        assert "name" in p
        assert "status" in p
        assert "memory_percent" in p
        assert "cpu_percent" in p
        # Data minimization: command-line arguments and environment must not exist
        assert "cmdline" not in p
        assert "command" not in p
        assert "environ" not in p
        assert "cwd" not in p

    # Test filtering and CPU sorting
    filtered = asyncio.run(tool.execute(ProcessListInput(filter_name="python", limit=5, sort_by="cpu")))
    assert filtered["returned_count"] <= 5
    for proc in filtered["processes"]:
        assert "python" in proc["name"].lower()


# 5. Process inspection cannot terminate or launch processes
def test_5_process_list_has_no_termination_or_launch_methods():
    tool = ProcessListTool()
    forbidden_methods = [
        "kill", "terminate", "suspend", "resume", "launch",
        "spawn", "start", "stop", "restart", "execute", "exec"
    ]
    for method in forbidden_methods:
        if method == "execute":
            # execute is the safe Tool contract method, but verify it only returns data
            assert callable(getattr(tool, "execute"))
        else:
            assert not hasattr(tool, method), f"ProcessListTool must not have '{method}' method"


# 6. Application list tool returns safe structured data
def test_6_application_list_returns_safe_metadata():
    tool = ApplicationListTool()
    data = asyncio.run(tool.execute(ApplicationListInput(limit=20)))
    assert "count" in data
    assert "applications" in data
    assert isinstance(data["applications"], list)
    for app in data["applications"]:
        assert "name" in app
        assert "kind" in app
        # Ensure name is bounded
        assert len(app["name"]) <= 100


# 7. Tools are registered correctly in application
def test_7_tools_registered_in_app():
    app = create_app()
    registry: ToolRegistry = app.state.executor.registry
    tool_names = [t.name for t in registry.list_tools()]
    assert "system_info" in tool_names
    assert "system_time" in tool_names
    assert "resource_metrics" in tool_names
    assert "process_list" in tool_names
    assert "application_list" in tool_names


# 8. SAFE permission is enforced
def test_8_safe_permission_level_enforced():
    tools = [SystemInfoTool(), SystemTimeTool(), ResourceMetricsTool(), ProcessListTool(), ApplicationListTool()]
    for tool in tools:
        assert tool.permission_level is PermissionLevel.SAFE


# 9. SAFE tools still pass through Executor
def test_9_safe_tools_pass_through_executor(tmp_path):
    executor = make_executor(tmp_path)
    res = asyncio.run(executor.execute("system_time", {}))
    assert "local_time" in res
    assert "timezone_name" in res


# 10. Audit events are generated for SAFE tool execution
def test_10_audit_events_are_generated(tmp_path):
    executor = make_executor(tmp_path)
    asyncio.run(executor.execute("system_info", {}))
    events = executor.audit_events
    event_types = [e.event_type for e in events if e.tool == "system_info"]
    assert "tool_requested" in event_types
    assert "validation_succeeded" in event_types
    assert "permission_decision" in event_types
    assert "execution_started" in event_types
    assert "execution_succeeded" in event_types


# 11. Tool input validation works
def test_11_tool_input_validation(tmp_path):
    executor = make_executor(tmp_path)
    # disk_path with parent traversal must fail validation
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("resource_metrics", {"disk_path": "C:\\..\\windows"}))

    # disk_path with null bytes must fail validation
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("resource_metrics", {"disk_path": "C:\\\x00\\bad"}))

    # process limit out of bounds (le=200)
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("process_list", {"limit": 500}))

    # process invalid sort_by
    with pytest.raises(InvalidToolInputError):
        asyncio.run(executor.execute("process_list", {"sort_by": "dangerous_eval"}))


# 12. Tool output does not expose environment secrets
def test_12_tool_output_does_not_expose_environment_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_API_KEY", "SECRET_KEY_12345_XYZ")
    monkeypatch.setenv("DATABASE_PASSWORD", "VERY_SECRET_DB_PASSWORD")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SUPER_SECRET_AWS_KEY")
    executor = make_executor(tmp_path)

    for tool_name, payload in [
        ("system_info", {}),
        ("system_time", {}),
        ("resource_metrics", {}),
        ("process_list", {"limit": 50}),
        ("application_list", {"limit": 50}),
    ]:
        result = asyncio.run(executor.execute(tool_name, payload))
        result_json = json.dumps(result)
        assert "SECRET_KEY_12345_XYZ" not in result_json
        assert "VERY_SECRET_DB_PASSWORD" not in result_json
        assert "SUPER_SECRET_AWS_KEY" not in result_json


# 13. Tool output does not expose credentials or tokens
def test_13_tool_output_does_not_expose_credentials_or_tokens(tmp_path, monkeypatch):
    test_token = "bearer-secret-token-abcdef123456"
    monkeypatch.setenv("AUTH_BEARER_TOKEN", test_token)
    executor = make_executor(tmp_path)

    for tool_name, payload in [
        ("system_info", {}),
        ("system_time", {}),
        ("resource_metrics", {}),
        ("process_list", {"limit": 50}),
        ("application_list", {"limit": 50}),
    ]:
        result = asyncio.run(executor.execute(tool_name, payload))
        result_json = json.dumps(result)
        assert test_token not in result_json


# 14. Unauthorized callers cannot invoke the tools via API
def test_14_unauthorized_callers_cannot_invoke_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(tmp_path / "jarvis.sqlite3"))
    monkeypatch.setenv("JARVIS_AUTH_TOKENS", json.dumps([
        {"subject": "owner", "role": "owner", "token": "test-owner-token-12345"},
        {"subject": "auditor", "role": "auditor", "token": "test-auditor-token-12345"},
    ]))
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    # Missing auth header -> 401
    assert client.post("/actions/system_time/execute", json={"input": {}}).status_code == 401

    # Auditor role has no EXECUTE permission -> 403
    auditor_headers = {"Authorization": "Bearer test-auditor-token-12345"}
    assert client.post("/actions/system_time/execute", headers=auditor_headers, json={"input": {}}).status_code == 403

    # Owner role has EXECUTE permission -> 200
    owner_headers = {"Authorization": "Bearer test-owner-token-12345"}
    res = client.post("/actions/system_time/execute", headers=owner_headers, json={"input": {}})
    assert res.status_code == 200
    assert "local_time" in res.json()


# 15. Tool failures return safe structured errors
def test_15_tool_failures_return_safe_structured_errors():
    tool = ResourceMetricsTool()
    # Non-existent volume letter
    data = asyncio.run(tool.execute(ResourceMetricsInput(disk_path="Z:\\nonexistent_volume")))
    assert data["disk"]["error"] == "volume_unavailable"
    assert data["disk"]["path"] == "Z:\\nonexistent_volume"


# 16. Agent executes Phase 6 tools end-to-end through state machine
def test_16_agent_executes_all_phase6_tools_end_to_end(tmp_path):
    executor = make_executor(tmp_path)
    agent = Agent(executor=executor)

    # Test all Phase 6 tools executed via Agent.execute_step()
    for tool_name, payload in [
        ("system_info", {}),
        ("system_time", {}),
        ("resource_metrics", {"disk_path": "C:\\"}),
        ("process_list", {"limit": 10}),
        ("application_list", {"limit": 10}),
    ]:
        state = agent.prepare(f"inspect {tool_name}")
        res = asyncio.run(agent.execute_step(state, tool_name, payload))
        assert res.status is AgentStatus.COMPLETED
        assert res.result is not None
        assert res.error is None

        # Verify durable persistence in SQLite
        saved = agent.get_session(state.session_id)
        assert saved.status is AgentStatus.COMPLETED
        assert saved.result == res.result
