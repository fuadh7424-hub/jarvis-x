"""SQLite repositories for approvals and append-only audit events."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.agent.state import AgentState, AgentStatus
from app.security.approvals import ApprovalRequest, ApprovalStatus, AuditEvent
from app.security.permissions import PermissionLevel


class ApprovalStateError(ValueError):
    pass


class SQLiteRepository:
    """Small repository using parameterized queries and short write transactions."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    permission_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    resolution TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    action TEXT NOT NULL,
                    permission_level TEXT NOT NULL,
                    approval_id TEXT,
                    approval_status TEXT,
                    outcome TEXT,
                    error_category TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    goal TEXT,
                    action TEXT,
                    tool_name TEXT,
                    input_digest TEXT,
                    approval_id TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
                CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp ON audit_events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status);
                """
            )

    @staticmethod
    def _time(value: datetime | None) -> str | None:
        return value.astimezone(timezone.utc).isoformat() if value else None

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    def _approval(self, row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            id=UUID(row["approval_id"]), action=row["action"], reason="Approval requested",
            requested_by=row["requested_by"], risk_level=PermissionLevel(row["permission_level"]),
            tool_name=row["tool_name"], input_digest=row["input_digest"],
            timestamp=self._parse(row["created_at"]), status=ApprovalStatus(row["status"]),
            resolved_at=self._parse(row["resolved_at"]), expires_at=self._parse(row["expires_at"]),
            consumed_at=self._parse(row["consumed_at"]), resolution=row["resolution"],
        )

    def create_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(request.id), request.action, request.tool_name, request.input_digest,
                 request.risk_level.value, request.status.value, request.requested_by,
                 self._time(request.timestamp), self._time(request.resolved_at), self._time(request.expires_at),
                 self._time(request.consumed_at), request.resolution, "{}"),
            )
        return request

    def get(self, request_id: UUID) -> ApprovalRequest:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (str(request_id),)).fetchone()
        if row is None:
            raise KeyError(request_id)
        return self._approval(row)

    def list(self) -> tuple[ApprovalRequest, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()
        return tuple(self._approval(row) for row in rows)

    def resolve(self, request_id: UUID, approved: bool, now: datetime) -> ApprovalRequest:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (str(request_id),)).fetchone()
            if row is None:
                raise KeyError(request_id)
            request = self._approval(row)
            if request.status is not ApprovalStatus.PENDING:
                raise ApprovalStateError("Approval request is not pending")
            if request.expires_at and request.expires_at <= now:
                connection.execute("UPDATE approvals SET status = ?, resolved_at = ?, resolution = ? WHERE approval_id = ?", (ApprovalStatus.EXPIRED.value, self._time(now), "expired", str(request_id)))
            else:
                status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                resolution = "approved" if approved else "rejected"
                connection.execute("UPDATE approvals SET status = ?, resolved_at = ?, resolution = ? WHERE approval_id = ?", (status.value, self._time(now), resolution, str(request_id)))
        return self.get(request_id)

    def consume(self, request_id: UUID, tool_name: str, input_digest: str, now: datetime) -> ApprovalRequest:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (str(request_id),)).fetchone()
            if row is None:
                raise KeyError(request_id)
            request = self._approval(row)
            if request.tool_name != tool_name or request.input_digest != input_digest:
                raise ApprovalStateError("Approval does not match this action")
            if request.status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED) and request.expires_at and request.expires_at <= now:
                connection.execute("UPDATE approvals SET status = ?, resolved_at = ?, resolution = ? WHERE approval_id = ?", (ApprovalStatus.EXPIRED.value, self._time(now), "expired", str(request_id)))
            elif request.status is not ApprovalStatus.APPROVED:
                raise ApprovalStateError(f"Approval request is {request.status.value}")
            else:
                changed = connection.execute("UPDATE approvals SET status = ?, consumed_at = ?, resolution = ? WHERE approval_id = ? AND status = ?", (ApprovalStatus.CONSUMED.value, self._time(now), "consumed", str(request_id), ApprovalStatus.APPROVED.value)).rowcount
                if changed != 1:
                    raise ApprovalStateError("Approval request was already consumed")
        return self.get(request_id)

    def append_audit_event(self, event: AuditEvent) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(event.id), self._time(event.timestamp), event.event_type, event.tool, event.action,
                 event.permission_level.value, str(event.approval_id) if event.approval_id else None,
                 event.approval_status.value if event.approval_status else None, event.result, event.error,
                 json.dumps(event.metadata, sort_keys=True)),
            )

    def list_audit_events(self, limit: int = 100) -> tuple[AuditEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM audit_events ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return tuple(AuditEvent(id=UUID(row["event_id"]), timestamp=self._parse(row["timestamp"]), event_type=row["event_type"], tool=row["tool"], action=row["action"], permission_level=PermissionLevel(row["permission_level"]), approval_id=UUID(row["approval_id"]) if row["approval_id"] else None, approval_status=ApprovalStatus(row["approval_status"]) if row["approval_status"] else None, result=row["outcome"], error=row["error_category"], metadata=json.loads(row["metadata_json"])) for row in rows)

    def _session(self, row: sqlite3.Row) -> AgentState:
        return AgentState(
            session_id=UUID(row["session_id"]),
            task_id=UUID(row["task_id"]),
            status=AgentStatus(row["status"]),
            goal=row["goal"],
            action=row["action"],
            tool_name=row["tool_name"],
            input_digest=row["input_digest"],
            approval_id=UUID(row["approval_id"]) if row["approval_id"] else None,
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            created_at=self._parse(row["created_at"]),
            updated_at=self._parse(row["updated_at"]),
            metadata=json.loads(row["metadata_json"]),
        )

    def save_session(self, state: AgentState) -> AgentState:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_sessions (
                    session_id, task_id, status, goal, action, tool_name,
                    input_digest, approval_id, result_json, error,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    status=excluded.status,
                    goal=excluded.goal,
                    action=excluded.action,
                    tool_name=excluded.tool_name,
                    input_digest=excluded.input_digest,
                    approval_id=excluded.approval_id,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    str(state.session_id),
                    str(state.task_id),
                    state.status.value,
                    state.goal,
                    state.action,
                    state.tool_name,
                    state.input_digest,
                    str(state.approval_id) if state.approval_id else None,
                    json.dumps(state.result) if state.result is not None else None,
                    state.error,
                    self._time(state.created_at),
                    self._time(state.updated_at),
                    json.dumps(state.metadata, sort_keys=True),
                ),
            )
        return state

    def get_session(self, session_id: UUID) -> AgentState:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agent_sessions WHERE session_id = ?", (str(session_id),)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return self._session(row)

    def list_sessions(self, limit: int = 100) -> tuple[AgentState, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM agent_sessions ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(self._session(row) for row in rows)

