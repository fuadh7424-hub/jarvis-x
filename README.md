# JARVIS-X

Local-first personal AI assistant for Windows. This repository implements **Phase 1 only**: a secure, typed application foundation.

## Architecture

- `app/core`: environment-driven settings and JSON structured logging.
- `app/agent`: state and a deliberately inert planning boundary.
- `app/tools`: typed tool contract and allow-listed registry.
- `app/security`: explicit `SAFE`, `CONFIRM`, and `BLOCKED` policy, transient approvals, and audit-event schema.
- `app/memory`: non-persistent memory protocol and in-memory reference implementation.
- `app/api`: FastAPI health endpoint.

Voice, browser automation, desktop automation, shell access, and persistent memory are explicitly out of scope.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/health` for `{ "status": "ok", "service": "jarvis-x" }`.

## Test

```powershell
pytest
```

## Security decisions

Tools are only addressable after registration. Each declares its input model and permission level. `BLOCKED` cannot be authorized; `CONFIRM` must receive an explicit approval before a later executor can act. Phase 1 contains no system-operation tools, therefore the agent has no shell, browser, desktop, mouse, keyboard, or voice control.

## Phase 2 recommendation

Add a user-visible approval UI and an audited executor that validates registered tool inputs before introducing one narrowly-scoped, read-only capability.
