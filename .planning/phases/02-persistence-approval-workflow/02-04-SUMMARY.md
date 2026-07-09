---
phase: 02-persistence-approval-workflow
plan: 04
subsystem: database
tags: [sqlalchemy, sqlite, audit-log, prompt-injection, gateway]

# Dependency graph
requires:
  - phase: 02-persistence-approval-workflow (02-01/02-02/02-03)
    provides: db.py async engine/session, models.py (Conversation/Message/Policy/PolicyRule/ApprovalRequest), gateway.py ToolExecutionGateway with DENY/REQUIRE_APPROVAL/ALLOW branches
provides:
  - ToolExecution and AuditLog ORM models (final two of the phase's seven tables)
  - gateway.execute_tool persists a tool_executions/audit_logs row on every branch (DENY, approval-denied, ALLOW/executed)
  - scan_for_prompt_injection: logging-only heuristic flag (SEC-02/D-06/D-07), never fed into PolicyContext/evaluate()
affects: [03-dashboard-realtime]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Audit row persisted via a fresh session per branch (_persist_tool_execution), same session_factory-per-call convention as _persist_approval_request"
    - "Logging-only detection signal kept as a sibling column, never merged into the policy input dataclass (POLICY-01/SEC-01 invariant)"

key-files:
  created: [backend/test_audit.py]
  modified: [backend/models.py, backend/gateway.py]

key-decisions:
  - "Removed the Phase 1 [POLICY]/[APPROVAL]/[RESULT] print statements now that DB rows are the authoritative audit trail (plan left this to executor discretion)"
  - "REQUIRE_APPROVAL-denied branch persists decision_action=REQUIRE_APPROVAL with decision_reason from the original policy decision, and the approve/reject outcome in result_error, keeping the two concerns (why approval was required vs. how it resolved) on separate columns"

patterns-established:
  - "scan_for_prompt_injection(tool_name, content) -> bool: pure function, hardcoded phrase list including the exact SANDBOX-03 fixture substring, scoped to _SCANNED_TOOLS only"

requirements-completed: [SEC-02, DB-01]

# Metrics
duration: 25min
completed: 2026-07-09
---

# Phase 02 Plan 04: Tool Execution Audit Trail + Prompt-Injection Flag Summary

**Every gateway.execute_tool() branch (DENY, approval-denied, ALLOW/executed) now writes a durable ToolExecution row, with a logging-only PROMPT_INJECTION_SUSPECTED flag on read/external-content tool output that never reaches the Policy Engine.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-07-09T19:23:00Z (approx.)
- **Completed:** 2026-07-09T19:48:28Z
- **Tasks:** 2 completed
- **Files modified:** 3 (2 modified, 1 created)

## Accomplishments
- `ToolExecution` and `AuditLog` models added — all seven tables for this phase now exist
- `gateway.execute_tool` persists request + policy decision + result on every code path, not just the executed path
- `scan_for_prompt_injection` heuristic added, verified against the real `injected_instructions.txt` fixture wording, and proven (by test) to never touch `PolicyContext`

## Task Commits

Each task was committed atomically:

1. **Task 1: Failing tests — audit rows per branch + injection flag (logging-only)** - `eac5193` (test)
2. **Task 2: ToolExecution/AuditLog models + gateway audit writes + injection scan** - `97d8585` (feat)

**Plan metadata:** (this commit, added by worktree merge)

## Files Created/Modified
- `backend/test_audit.py` - RED/GREEN tests: scan scope + exact fixture wording, per-branch persistence (ALLOW, DENY), PolicyContext-never-has-the-flag invariant
- `backend/models.py` - `ToolExecution` (request/decision/result columns + `flagged_prompt_injection`) and `AuditLog` (`event`, `detail`, `flags`) models
- `backend/gateway.py` - `scan_for_prompt_injection` + `_SUSPICIOUS_PHRASES`/`_SCANNED_TOOLS`, `_persist_tool_execution` helper, wired into all three `execute_tool` branches (policy-exception, DENY, approval-denied, ALLOW/executed)

## Decisions Made
- Dropped the old `[POLICY]`/`[APPROVAL]`/`[RESULT]` `print()` statements since the DB rows are now the authoritative trail (plan explicitly left this to executor discretion) — reduces noise, no test depended on stdout.
- The policy-engine-exception branch persists `decision_action="DENY"` with the exception-derived reason and an empty `matched_rule_ids` list, consistent with the existing fail-closed synthesized `ToolResult` for that path.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All seven tables for Phase 02 (`conversations`, `messages`, `policies`, `policy_rules`, `approval_requests`, `tool_executions`, `audit_logs`) now exist and are exercised end-to-end by the gateway.
- Phase 03 (dashboard/realtime) can read `tool_executions`/`audit_logs` directly for the Audit Logs page; no schema changes anticipated.
- Manual SEC-02 proof (reading `injected_instructions.txt` live via `/chat` and inspecting the resulting row) is described in the plan's `<verification>` block but requires a live `GEMINI_API_KEY` session — not exercised in this automated-only plan; automated tests already cover the scan function against the exact fixture wording and the persistence path.

---
*Phase: 02-persistence-approval-workflow*
*Completed: 2026-07-09*

## Self-Check: PASSED

All created/modified files (backend/test_audit.py, backend/models.py, backend/gateway.py, this SUMMARY.md) confirmed present on disk; task commits eac5193, 97d8585, and metadata commit cecd890 confirmed in git log.
