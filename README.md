# JARVIS-X

Local-first personal AI assistant for Windows. Current status: Phase 10B (Controlled Mouse, Keyboard & Safe Hotkey Input) with durable local approvals, role-based API security, sandboxed system inspection, read-only browser state awareness, controlled browser actions, read-only desktop awareness, controlled window layout and state management, and permission-gated, validated computer input actions.

## Architecture

- `app/core`: environment-driven settings and JSON structured logging.
- `app/agent`: 9-state session state machine, planner boundary, and executor-orchestrated agent loop.
- `app/tools`: typed tool contracts, allow-listed registry, read-only awareness tools, approval-gated browser action tools, approval-gated window action tools, and approval-gated desktop input tools.
- `app/browser`: read-only browser inspector and controlled action controller supporting local CDP endpoints, deterministic DOM target validation, sensitive input blocking, and URL sanitization.
- `app/desktop`: read-only desktop inspector (window enumeration, active focus detection, display geometry, title sanitization), controlled desktop action controller (window focus, state transitions, geometry bounds clamping), and controlled desktop input controller (safe mouse movement, clicking, text entry, and hotkey injection).

- `app/security`: default-deny `SAFE`, `CONFIRM`, and `BLOCKED` policy, approval lifecycle models, bearer token authentication, and RBAC authorization (`OWNER`, `OPERATOR`, `AUDITOR`, `READ_ONLY`).
- `app/storage`: local SQLite repository with WAL mode for durable audit events, one-time approvals, and agent session state.
- `app/memory`: non-persistent memory protocol and in-memory reference implementation.
- `app/api`: protected health, approval, execution, and audit inspection endpoints.

## Registered SAFE Tools (Read-Only)

Every read-only tool enforces `PermissionLevel.SAFE`, validates input with Pydantic, and executes strictly through `Executor.execute()` with full audit logging:

1. `text_statistics`: Deterministic character, word, and line counting.
2. `system_info`: Inspect OS name, version, machine architecture, hostname, and Python runtime.
3. `system_time`: Inspect local time, UTC time, timezone name, UTC offset, and calendar info.
4. `resource_metrics`: Inspect CPU usage percent, virtual memory (total, used, available, percent), and volume disk space.
5. `process_list`: Inspect currently running processes (PID, name, status, memory percent, CPU percent). Data-minimized: command-line arguments and environment variables are strictly omitted. Cannot terminate or launch processes.
6. `application_list`: Inspect active user applications and visible window titles safely.
7. `browser_status`: Inspect supported browser availability, running browser processes, and local inspection connection status.
8. `browser_tabs`: Inspect open browser tabs (titles, sanitized URLs, active state) safely without modifying browser state.
9. `active_tab`: Inspect active browser tab URL and title only.
10. `page_metadata`: Inspect safe high-level page metadata (domain, protocol, title, security status) without reading private storage or executing scripts.
11. `desktop_windows`: Enumerate visible top-level application windows safely with sanitized titles and owning process metadata.
12. `desktop_active_window`: Inspect the currently focused top-level desktop window (title, process, bounds) safely.
13. `desktop_displays`: Enumerate connected displays, resolutions, bounds, and work areas safely without capturing screen pixels.

## Registered Mutating Tools (PermissionLevel.CONFIRM)

Every mutating tool enforces `PermissionLevel.CONFIRM`, requires explicit one-time user approval, validates targets deterministically, and performs post-action verification:

### Browser Actions (Phase 8)
1. `browser_navigate`: Navigate browser to a validated HTTP/HTTPS URL with post-action verification. Schemes such as `javascript:`, `data:`, `file:`, `chrome:` are strictly rejected. Embedded credentials and sensitive query tokens are sanitized.
2. `browser_click`: Click a validated DOM or accessible target (`selector`, `element_id`, `role`, `name`) on the current page without screen coordinates or arbitrary script injection.
3. `browser_type`: Type non-sensitive text into a validated target element. Inputs containing passwords, API keys, or tokens are rejected. Raw text is never recorded in audit logs.
4. `browser_new_tab`: Open a new browser tab with an optional validated destination URL and verify tab creation.

### Desktop Window Actions (Phase 10A)
5. `desktop_window_focus`: Safely focus and activate an existing visible top-level application window with post-action active focus verification. Nonexistent window IDs are rejected.
6. `desktop_window_state`: Safely minimize, maximize, or restore an existing top-level application window with post-action window placement verification. Application termination or closing is blocked.
7. `desktop_window_move_resize`: Safely move and/or resize an existing top-level window within monitor work areas. Enforces minimum dimensions (200x150) and clamps coordinates to display bounds to prevent off-screen window loss.

### Controlled Desktop Input Actions (Phase 10B)
8. `desktop_mouse_move`: Safely move mouse cursor to validated screen coordinates with display work area bounds clamping and post-action cursor position verification. Unbounded coordinates, negative coordinates outside multi-monitor geometry, and non-integer values are rejected.
9. `desktop_mouse_click`: Safely execute a mouse click (`left_click`, `right_click`, or `double_left_click`) at validated coordinates or current cursor position with post-action verification. Unsupported click types (middle clicks, arbitrary drags) are rejected.
10. `desktop_keyboard_type`: Type non-sensitive text into the currently active top-level desktop window. Enforces a maximum length limit of 500 characters, rejects null bytes and control characters, blocks sensitive credentials/tokens/passwords, never reads the clipboard or environment variables, and minimizes audit metadata to text length rather than raw text.
11. `desktop_hotkey`: Trigger an allowlisted safe keyboard shortcut (`CTRL+C`, `CTRL+V`, `CTRL+X`, `CTRL+A`, `CTRL+Z`, `CTRL+Y`, `CTRL+S`, `CTRL+F`, `ALT+TAB`, `ESC`, `ENTER`, `BACKSPACE`, `TAB`, `SPACE`, `DELETE`, `UP`, `DOWN`, `LEFT`, `RIGHT`, `PAGEUP`, `PAGEDOWN`, `HOME`, `END`). Destructive combinations (`CTRL+ALT+DELETE`, `ALT+F4`, `CTRL+SHIFT+ESC`, `WIN+L`, etc.) and arbitrary unallowlisted key combinations are strictly rejected via schema validation.

### Secure Screen Awareness Actions (Phase 10C)
12. `desktop_screen_capture`: Safely capture screen image metadata and visual state. Requires explicit user confirmation (`PermissionLevel.CONFIRM`). Supports capturing the primary display, a specified validated display monitor (e.g. `display-1`), or all displays spanned together. Returns structured, safe metadata (dimensions, format, timestamp, size). Keeps screenshots in memory or controlled temporary paths outside repository tracking with automatic cleanup. Pixels and base64 payloads are strictly excluded from audit logs.

## Stage 10C Security Boundaries & Verification Scope

All Stage 10C screen awareness tools operate under strict security boundaries:
- **PermissionLevel**: Strictly enforced at `PermissionLevel.CONFIRM`. Screen capture cannot execute without explicit one-time user approval.
- **Data Minimization**: Screenshot pixels and base64 buffers are never written into audit logs. Audit events record only operation metadata, timestamps, and outcomes.
- **Controlled Storage**: Screenshots are never placed in Git-tracked directories. Temporary image files are stored in a controlled temporary folder with explicit disposal APIs. No permanent archives are created.
- **No Autonomous Targeting**: Screen capture is strictly an observation capability. Autonomous visual clicking, visual targeting, and OCR-driven actions are forbidden.
- **Provider Isolation**: Native screen capture is encapsulated behind `ScreenCaptureProvider`. Future OCR and vision capabilities are abstracted (`OCRProvider`, `VisionProvider`) without automatic external AI transmissions.

## What JARVIS Cannot Do Yet (Deferred Capabilities & Security Restrictions)

The following capabilities are strictly forbidden and out of scope in Phase 10C:
- Autonomous visual clicking or targeting (Phase 10D - NOT implemented)
- Autonomous mouse targeting or OCR-driven clicking
- Automatic transmission or streaming of screenshots to external AI providers
- Autonomous multi-step computer control workflows (NOT implemented)
- Mouse drag-and-drop, button holding, or arbitrary mouse-down duration (Deferred)
- Uncontrolled scrolling or mouse wheel automation (Deferred)
- Arbitrary key combination generation or unallowlisted hotkeys (Prohibited)
- Destructive system hotkeys (`CTRL+ALT+DELETE`, `ALT+F4`, task manager shortcuts, shutdown shortcuts)
- Application closing, process killing, or task termination
- Arbitrary JavaScript execution or script injection (`eval`, `evaluate`, `execute_script`)
- Continuous screen recording or video capture
- Cookie, local storage, session storage, or password extraction
- Entering passwords, API keys, or authentication tokens into input fields
- Reading clipboard contents automatically or extracting credentials
- Arbitrary shell or subprocess execution (`cmd`, PowerShell, bash)
- Filesystem creation, modification, or deletion
- Financial transactions, orders, or sending emails

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/health` for `{ "status": "ok", "service": "jarvis-x" }`.

## Local Storage

SQLite is configured through `JARVIS_DATABASE_PATH` (default: `data/jarvis-x.sqlite3`) with WAL mode enabled. Relative paths may not use parent traversal (`..`). The application creates parent directories and schema safely at startup. `JARVIS_APPROVAL_TTL_SECONDS` controls approval expiration (default: 300 seconds).

Approval records retain an input digest rather than the submitted input. Audit events retain only event categories, outcomes, and safe identifiers; they intentionally exclude tool inputs, reasons, actor credentials, and exception text.

## Test

```powershell
$env:TEMP=(Resolve-Path .pytest-temp).Path
$env:TMP=$env:TEMP
python -m pytest -p no:cacheprovider
```
