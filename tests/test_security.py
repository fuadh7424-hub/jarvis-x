import json

from app.core.logging import configure_logging, log_audit_event
from app.security.approvals import ApprovalRequest, ApprovalStatus, AuditEvent
from app.security.permissions import PermissionLevel, PermissionPolicy


def test_permission_classification():
    assert PermissionPolicy.allows(PermissionLevel.SAFE)
    assert not PermissionPolicy.allows(PermissionLevel.CONFIRM)
    assert PermissionPolicy.allows(PermissionLevel.CONFIRM, approved=True)


def test_blocked_action_behavior():
    assert not PermissionPolicy.allows(PermissionLevel.BLOCKED, approved=True)


def test_approval_lifecycle_contract():
    request = ApprovalRequest(action="execute", reason="test", requested_by="agent", risk_level=PermissionLevel.CONFIRM)
    assert request.status is ApprovalStatus.PENDING
    assert ApprovalStatus.CONSUMED.value == "consumed"


def test_audit_event_contract():
    event = AuditEvent(tool="example", action="read", permission_level=PermissionLevel.SAFE, result="ok")
    assert event.result == "ok"


def test_audit_logging(tmp_path):
    logger = configure_logging(tmp_path)
    log_audit_event(AuditEvent(tool="example", action="read", permission_level=PermissionLevel.SAFE))
    for handler in logger.handlers:
        handler.flush()
    records = (tmp_path / "jarvis-x.log").read_text(encoding="utf-8").splitlines()
    assert json.loads(records[-1])["event"]["tool"] == "example"
