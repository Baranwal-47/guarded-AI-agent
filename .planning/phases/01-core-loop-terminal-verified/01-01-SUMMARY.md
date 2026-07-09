---
phase: 01-core-loop-terminal-verified
plan: 01
subsystem: mcp
tags: [mcp, fastmcp, stdio, sandbox, path-confinement, prompt-injection]

# Dependency graph
requires: []
provides:
  - "Sandbox File Manager MCP server (stdio) exposing list_files/read_file/write_file/move_file/delete_file"
  - "Server-side path confinement (_resolve_within_sandbox) independent of any policy engine"
  - "Honeypot and prompt-injection demo fixtures under sandbox/"
  - "Standalone test_client.py proving live discovery + confinement + inert injection read"
affects: [01-02, 01-03, 01-04, 01-05]

# Tech tracking
tech-stack:
  added: ["mcp>=1.28,<2 (FastMCP)"]
  patterns:
    - "_resolve_within_sandbox() helper called at the top of every handler; resolves + checks candidate == SANDBOX_ROOT or SANDBOX_ROOT in candidate.parents"
    - "Handlers return structured 'ERROR: ...' strings instead of raising to the MCP transport"
    - "All diagnostics printed to sys.stderr only; stdout reserved for JSON-RPC framing"

key-files:
  created:
    - mcp-servers/sandbox-file-manager/server.py
    - mcp-servers/sandbox-file-manager/pyproject.toml
    - mcp-servers/sandbox-file-manager/test_client.py
    - mcp-servers/sandbox-file-manager/sandbox/notes.txt
    - mcp-servers/sandbox-file-manager/sandbox/secrets.txt
    - mcp-servers/sandbox-file-manager/sandbox/injected_instructions.txt
  modified:
    - .gitignore

key-decisions:
  - "Added .venv/, __pycache__/, *.pyc to root .gitignore (Rule 2 - missing critical: uv sync would otherwise leave a Python venv untracked/uncommittable-by-accident in a repo with no Python gitignore entries yet)"
  - "uv.lock committed for reproducible installs, matching the project's uv-based workflow documented in the mcp-development skill"

patterns-established:
  - "Pattern: MCP tool handlers resolve-then-check-then-act, never trust raw path strings"
  - "Pattern: stdio server diagnostics to stderr only, verified by piping stdout alone in test runs"

requirements-completed: [SANDBOX-01, SANDBOX-02, SANDBOX-03]

# Metrics
duration: ~15min
completed: 2026-07-09
---

# Phase 01 Plan 01: Sandbox File Manager MCP Server Summary

**FastMCP stdio server with 5 file tools, server-side sandbox-root path confinement, and honeypot/prompt-injection demo fixtures, verified end-to-end by a standalone stdio client.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-09T11:38:34Z
- **Tasks:** 2
- **Files modified:** 7 (6 created, 1 modified)

## Accomplishments
- Built `server.py`: FastMCP("sandbox-file-manager") over stdio, exactly 5 `@mcp.tool()` handlers (`list_files`, `read_file`, `write_file`, `move_file`, `delete_file`)
- `_resolve_within_sandbox()` enforces SANDBOX-02 defense-in-depth: rejects relative escapes (`../escape.txt`) and absolute-outside paths (`/etc/passwd`), accepts normalized in-root paths (`subdir/../notes.txt`) — verified with all 4 confinement cases from the plan's automated check
- Seeded `notes.txt` (benign), `secrets.txt` (honeypot with fake `AWS_SECRET_ACCESS_KEY`, SANDBOX-03), and `injected_instructions.txt` (blunt indirect-prompt-injection fixture) under `sandbox/`
- `test_client.py` spawns the real server subprocess via `stdio_client`, verifies live `tools/list` discovery of all 5 tools, confirms reading the injection fixture returns literal inert text, confirms `read_file('../server.py')` is rejected with the structured out-of-sandbox error (not file contents, not a traceback), and exercises a multi-entry `list_files('.')` payload — all PASS, exit 0

## Task Commits

Each task was committed atomically:

1. **Task 1: FastMCP server with 5 tools and resolved-path sandbox confinement** - `090ca03` (feat)
2. **Task 2: Seed demo fixtures and a standalone discovery/confinement verification client** - `39f2593` (test)

_Note: SUMMARY.md commit follows this document._

## Files Created/Modified
- `mcp-servers/sandbox-file-manager/server.py` - FastMCP stdio server, 5 tools, path-confinement helper
- `mcp-servers/sandbox-file-manager/pyproject.toml` - package metadata, pins `mcp>=1.28,<2`
- `mcp-servers/sandbox-file-manager/test_client.py` - standalone stdio verification client
- `mcp-servers/sandbox-file-manager/sandbox/notes.txt` - benign fixture
- `mcp-servers/sandbox-file-manager/sandbox/secrets.txt` - honeypot fake secret (SANDBOX-03)
- `mcp-servers/sandbox-file-manager/sandbox/injected_instructions.txt` - prompt-injection fixture
- `.gitignore` - added Python `.venv/`, `__pycache__/`, `*.pyc` entries

## Decisions Made
- Confirmed `uv sync` resolves `mcp==1.28.1` (matches CLAUDE.md's pinned stable version, not the 2.0.0b1 pre-release)
- `move_file` confines both `source` and `destination` independently (two `_resolve_within_sandbox` calls), per plan requirement
- Handlers never raise `ValueError` to the transport — always caught and converted to a structured `"ERROR: ..."` string, so a bad path never surfaces as a raw traceback to the model

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added Python entries to root `.gitignore`**
- **Found during:** Task 1 (server scaffolding)
- **Issue:** Repo had no `.venv/`/`__pycache__/`/`*.pyc` ignore entries; `uv sync` creates a `.venv/` that would otherwise risk being accidentally staged
- **Fix:** Added `.venv/`, `__pycache__/`, `*.pyc` to `.gitignore`
- **Files modified:** `.gitignore`
- **Verification:** `git status --short` after `uv sync` shows no `.venv/`/`__pycache__/` as untracked
- **Committed in:** `090ca03` (part of Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Prevents accidental commit of a Python virtualenv; no scope creep.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Sandbox File Manager server is standalone-verified and ready to be wired into `mcp_manager.py` (plan 01-02+) via stdio transport
- Fixture files (`secrets.txt`, `injected_instructions.txt`) are in place for the Policy Engine / gateway demo beats later in this phase
- No blockers

---
*Phase: 01-core-loop-terminal-verified*
*Completed: 2026-07-09*
