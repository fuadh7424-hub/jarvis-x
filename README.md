# JARVIS-X

Local-first personal AI assistant for Windows. This repository implements Phase 3: a secure, typed foundation with durable local approvals and audit records.

## Architecture

- `app/core`: environment-driven settings and JSON structured logging.
- `app/agent`: state and a deliberately inert planning boundary.
- `app/tools`: typed tool contract and allow-listed registry.
- `app/security`: explicit `SAFE`, `CONFIRM`, and `BLOCKED` policy, approval lifecycle models, and an authorization boundary for future authentication.
- `app/storage`: local SQLite repository for durable, append-only audit events and one-time approvals.
- `app/memory`: non-persistent memory protocol and in-memory reference implementation.
- `app/api`: health, approval, controlled-execution, and read-only audit inspection endpoints.

Voice, browser automation, desktop automation, shell access, persistent memory, and LLM integration are explicitly out of scope.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/health` for `{ "status": "ok", "service": "jarvis-x" }`.

## Local storage

SQLite is configured through `JARVIS_DATABASE_PATH` (default: `data/jarvis-x.sqlite3`). Relative paths may not use parent traversal. The application creates the parent directory and schema safely at startup. `JARVIS_APPROVAL_TTL_SECONDS` controls approval expiration (default: 300 seconds).

Approval records retain an input digest rather than the submitted input. Audit events retain only event categories and safe identifiers; they intentionally exclude tool inputs, reasons, actor identity, and exception text.

## Test

```powershell
pytest
```

## Security decisions

Tools are only addressable after registration. Each declares its input model and permission level. `BLOCKED` cannot be authorized; `CONFIRM` must receive a matching, unexpired approval before the executor atomically consumes it. A consumed approval cannot be replayed.

The current HTTP API is local/development-oriented and has no authentication. It uses an explicit authorization-context boundary so authentication can be added without changing the executor. An approval ID alone is not authorization; deployment must restrict API access until authentication is added.

## Phase 4 recommendation

Add authenticated identity and role assignment at the API boundary before adding any additional capabilities.
