---
phase: 01-core-loop-terminal-verified
plan: 03
subsystem: mcp-manager
tags: [mcp, streamable-http, stdio, gemini-schema, asyncio, dual-transport]

# Dependency graph
requires: ["01-01 (Sandbox File Manager server)", "01-02 (backend scaffold, config.py)"]
provides:
  - "MCPManager: dual-transport connect_all(), live tool_name->server_name registry, server_for(), list_all_tools(), call() with structured ToolResult + single retry"
  - "ToolResult frozen dataclass (ok, content, error) — the one result shape the Gateway (01-04) re-returns"
  - "schema_sanitizer.sanitize_schema() — pure recursive JSON-Schema sanitizer for Gemini FunctionDeclarations"
  - "test_discovery.py — offline sanitizer pytest suite + live dual-server discovery/round-trip script"
affects: ["01-04 (Gateway imports MCPManager.call/ToolResult)", "01-05 (agent loop imports list_all_tools/server_for read-only)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "AsyncExitStack holds both transport context managers + both ClientSessions for process lifetime; aclose() unwinds it in LIFO order, terminating the stdio subprocess cleanly"
    - "call() separates transport-level failures (timeout/exception, retried once for idempotent tools) from content-level 'ERROR:' strings returned by the sandbox server (never retried, immediate ok=False)"
    - "sanitize_schema recurses by structural key (properties/items/anyOf|oneOf|allOf/$defs), not a flat walk, so nested MCP schemas sanitize to arbitrary depth without mutating the input"

key-files:
  created:
    - backend/schema_sanitizer.py
    - backend/mcp_manager.py
    - backend/test_discovery.py
  modified: []

key-decisions:
  - "Content-level 'ERROR:' string prefix from the sandbox server (server.py never raises to the transport, per 01-01's design) is treated by call() as ok=False — the plan's acceptance criteria required a path-escaping call to return ok=False, but the MCP transport itself sees no exception/isError, only literal text, so ToolResult synthesizes the failure from that convention"
  - "Retry-once applies only to transport-level failures (timeout/exception); a content-level 'ERROR:' response returns immediately without retry, since retrying a deterministic path-escape or not-found error cannot succeed"
  - "Created backend/.env locally (gitignored, never committed) with a placeholder GEMINI_API_KEY so get_settings() could instantiate for this plan's MCP-only verification — Settings requires gemini_api_key even though this plan never calls Gemini; a real key is needed starting 01-05"

patterns-established:
  - "Pattern: privileged call() always returns ToolResult, never raises — callers (Gateway) branch on .ok, no try/except needed at the call site"
  - "Pattern: registry (tool_to_server, schemas, descriptions) is built exclusively inside connect_all() from live tools/list — grep for tool-name literals in mcp_manager.py should only ever match the idempotent-retry allowlist"

requirements-completed: [MCP-01, MCP-02, MCP-03, MCP-04]

# Metrics
duration: ~25min
completed: 2026-07-09
---

# Phase 01 Plan 03: MCP Manager + Schema Sanitizer Summary

**Dual-transport MCPManager (stdio + Streamable HTTP behind one ClientSession interface) with a live-discovery-only tool registry, structured-error call() with single idempotent retry, and a recursive Gemini schema sanitizer — verified end-to-end against the real Context7 remote server and the real sandbox subprocess, not mocks.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-07-09
- **Tasks:** 2/2 completed
- **Files modified:** 3 created

## Accomplishments

- `backend/schema_sanitizer.py`: pure `sanitize_schema(schema: dict) -> dict` — recursively strips `additionalProperties`/`$schema`/`title`/`default`/`pattern`/`propertyNames` at any nesting depth (properties, items, anyOf/oneOf/allOf, $defs), renames `oneOf`->`anyOf`, never mutates input, warns via `logging.warning` when a behavior-changing key (`pattern`/`default`) is stripped
- `backend/mcp_manager.py`: `MCPManager` connects to the sandbox server (stdio subprocess) and Context7 (Streamable HTTP, real remote endpoint) through the same `ClientSession` interface, held alive via `AsyncExitStack`; `connect_all()` builds `_tool_to_server`/`_tool_schemas`/`_tool_descriptions` entirely from each server's live `tools/list` response; `server_for()` is a pure registry lookup; `call()` returns the frozen `ToolResult(ok, content, error)` in every branch (unknown tool, timeout, transport exception, or a content-level `"ERROR:"` string from the sandbox server), retrying exactly once only for the idempotent allowlist (`list_files`, `read_file`, `resolve-library-id`, `query-docs`); `aclose()` unwinds the stack, verified to leave no orphaned subprocess
- `backend/test_discovery.py`: 5 offline pytest sanitizer tests (all pass) plus a live `main()` script that connects both real servers, discovers 5 sandbox + 2 Context7 tools with zero hardcoded names, confirms `server_for("read_file") == "sandbox"`, round-trips a real `read_file` call, and confirms a path-escaping call returns a structured error, not an exception — all PASS against the live Context7 endpoint and the real sandbox subprocess

## Task Commits

Each task was committed atomically:

1. **Task 1: Pure JSON-Schema sanitizer for Gemini function declarations** (TDD) - `6ef77ee` (test, RED — ModuleNotFoundError) then `a1520c8` (feat, GREEN — 5/5 passing)
2. **Task 2: MCP Manager — dual-transport connect, live discovery registry, server_for() lookup, ToolResult, and call() with structured errors + single retry** - `4d97cf8` (feat)

_TDD task 1: RED (`6ef77ee`) confirmed failing via ModuleNotFoundError before GREEN (`a1520c8`) implemented schema_sanitizer.py. No REFACTOR commit needed — implementation was clean on first pass._

## Files Created/Modified

- `backend/schema_sanitizer.py` - `sanitize_schema()`, recursive, non-mutating, warns on behavior-changing strips
- `backend/mcp_manager.py` - `MCPManager`, `ToolResult`; dual-transport connect, live registry, `server_for()`, `call()`
- `backend/test_discovery.py` - 5 offline sanitizer unit tests + live `main()` discovery/round-trip script

## Decisions Made

- `call()`'s structured-error contract synthesizes `ok=False` from the sandbox server's `"ERROR:"`-prefixed text content (the server never raises to the transport, per 01-01's design — `read_file('../server.py')` returns normally with error text, not an MCP-level `isError`). The manager checks both `result.isError` (genuine protocol errors) and the `"ERROR:"` text convention, so both failure shapes converge on the same `ToolResult(ok=False, ...)`.
- Retry-once is scoped strictly to transport-level failures (timeout/exception) for idempotent tools; a content-level `"ERROR:"` response (e.g. path escape, not-found) returns immediately without retry since retrying cannot change a deterministic rejection.
- `backend/.env` created locally with a placeholder `GEMINI_API_KEY` (gitignored, not committed) purely to unblock `get_settings()` instantiation for this plan's MCP-only verification script — `Settings` requires `gemini_api_key` as a mandatory field even though nothing in this plan calls Gemini.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Local placeholder `backend/.env` to unblock `get_settings()` for MCP-only verification**
- **Found during:** Task 2 (running `test_discovery.py`'s live script)
- **Issue:** `backend/config.py`'s `Settings.gemini_api_key` has no default and is required; instantiating `get_settings()` (needed only for `context7_api_key`) fails with a validation error when no `.env` exists, blocking verification of a plan that never calls Gemini
- **Fix:** Created `backend/.env` (already gitignored per 01-02's `backend/.gitignore`, never committed) with a placeholder `GEMINI_API_KEY` value and empty `CONTEXT7_API_KEY`
- **Files modified:** `backend/.env` (untracked, gitignored — not part of this commit history)
- **Verification:** `uv run python test_discovery.py` runs to completion, `ALL PASS`
- **Committed in:** N/A — file is gitignored by design, matches `.env.example`'s intent that each developer supplies their own local `.env`

---

**Total deviations:** 1 auto-fixed (1 blocking, non-package, non-architectural). No scope creep — a real `GEMINI_API_KEY` is still required starting plan 01-05; this placeholder only unblocks MCP-only verification for this plan.
**Impact on plan:** None on `MCPManager`'s design — `config.py`/`Settings` untouched, no source file modified to accommodate this.

## Issues Encountered

None beyond the deviation above.

## User Setup Required

A real `GEMINI_API_KEY` must be set in `backend/.env` before plan 01-05 (agent loop) can run — the placeholder value created here is not a real key and must be replaced. `CONTEXT7_API_KEY` remains optional (Context7 works at lower rate limits without one, confirmed live in this plan's verification).

## Next Phase Readiness

- `MCPManager`/`ToolResult`/`sanitize_schema` public surfaces are stable and ready for `01-04`'s Gateway to import directly (`from mcp_manager import ToolResult`, `manager.call(...)`) — no interface changes anticipated.
- Live verification against the real Context7 remote server (not just the sandbox server) de-risks the Gemini tool-calling turn before the agent loop exists, per this plan's stated purpose.
- No orphaned subprocess confirmed via before/after process-count check — safe to wire `connect_all()`/`aclose()` into a FastAPI `lifespan` in a later plan without a known cleanup gap.
- No blockers for `01-04` (Gateway) or `01-05` (Agent Loop), other than the real `GEMINI_API_KEY` noted above.

---
*Phase: 01-core-loop-terminal-verified*
*Completed: 2026-07-09*
