import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.security.approvals import ApprovalRequest
from app.security.permissions import PermissionLevel
from main import create_app

TOKENS = {
    "owner": "owner-token-for-tests-12345",
    "operator": "operator-token-for-tests-12345",
    "auditor": "auditor-token-for-tests-12345",
    "readonly": "readonly-token-for-tests-12345",
}


def create_test_app(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("JARVIS_DATABASE_PATH", str(tmp_path / "jarvis.sqlite3"))
    monkeypatch.setenv("JARVIS_AUTH_TOKENS", json.dumps([
        {"subject": "owner", "role": "owner", "token": TOKENS["owner"]},
        {"subject": "operator", "role": "operator", "token": TOKENS["operator"]},
        {"subject": "auditor", "role": "auditor", "token": TOKENS["auditor"]},
        {"subject": "readonly", "role": "read_only", "token": TOKENS["readonly"]},
    ]))
    get_settings.cache_clear()
    return create_app()


def headers(role):
    return {"Authorization": f"Bearer {TOKENS[role]}"}


def test_health_endpoint_is_public(tmp_path, monkeypatch):
    response = TestClient(create_test_app(tmp_path, monkeypatch)).get("/health")
    assert response.status_code == 200


def test_authentication_rejects_missing_invalid_and_malformed_headers(tmp_path, monkeypatch):
    client = TestClient(create_test_app(tmp_path, monkeypatch))
    assert client.post("/actions/text_statistics/execute", json={"input": {"text": "one"}}).status_code == 401
    assert client.post("/actions/text_statistics/execute", headers={"Authorization": "Basic nope"}, json={"input": {"text": "one"}}).status_code == 401
    assert client.post("/actions/text_statistics/execute", headers={"Authorization": "Bearer invalid-token"}, json={"input": {"text": "one"}}).status_code == 401
    assert b"invalid-token" not in (tmp_path / "jarvis.sqlite3").read_bytes()


def test_owner_and_operator_can_execute_safe_tool(tmp_path, monkeypatch):
    client = TestClient(create_test_app(tmp_path, monkeypatch))
    for role in ("owner", "operator"):
        response = client.post("/actions/text_statistics/execute", headers=headers(role), json={"input": {"text": "one two\nthree"}})
        assert response.status_code == 200
        assert response.json()["word_count"] == 3


def test_auditor_and_read_only_cannot_execute_or_mutate(tmp_path, monkeypatch):
    client = TestClient(create_test_app(tmp_path, monkeypatch))
    for role in ("auditor", "readonly"):
        response = client.post("/actions/text_statistics/execute", headers=headers(role), json={"input": {"text": "one"}})
        assert response.status_code == 403
    response = client.post("/approvals", headers=headers("auditor"), json={"tool_name": "text_statistics", "input": {"text": "one"}, "reason": "test"})
    assert response.status_code == 403


def test_audit_access_and_approval_resolution_are_role_protected(tmp_path, monkeypatch):
    app = create_test_app(tmp_path, monkeypatch)
    client = TestClient(app)
    assert client.get("/audit-events", headers=headers("owner")).status_code == 200
    assert client.get("/audit-events", headers=headers("auditor")).status_code == 200
    assert client.get("/audit-events", headers=headers("readonly")).status_code == 403

    approval = ApprovalRequest(action="execute", reason="test", requested_by="owner", risk_level=PermissionLevel.CONFIRM, tool_name="future", input_digest="digest", expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
    app.state.executor.repository.create_approval(approval)
    assert client.post(f"/approvals/{approval.id}/resolve", headers=headers("operator"), json={"approved": True}).status_code == 403
    assert client.post(f"/approvals/{approval.id}/resolve", headers=headers("owner"), json={"approved": True}).status_code == 200


def test_authorization_happens_before_tool_execution(tmp_path, monkeypatch):
    app = create_test_app(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post("/actions/text_statistics/execute", headers=headers("auditor"), json={"input": {"text": "one"}})
    assert response.status_code == 403
    events = app.state.executor.repository.list_audit_events()
    assert any(event.event_type == "authorization_denied" for event in events)
    assert not any(event.event_type == "execution_succeeded" for event in events)
