---
phase: 03-dashboard-real-time-polish
plan: 02
subsystem: api
tags: [fastapi, sqlalchemy, policy-engine, rest]

# Dependency graph
requires:
  - phase: 03-dashboard-real-time-polish
    plan: 01
    provides: app.state.catalog / app.state.ws_manager stashed in lifespan
provides:
  - "GET /tools (thin ToolCatalog.list_all_tools() wrapper, D-05)"
  - "POST/GET /policies/rules, PATCH (toggle-only), DELETE /policies/rules/{id}"
  - "GET /approvals?status= (newest-first, PENDING branch shared with /chat/state)"
  - "GET /audit/executions, GET /audit/logs (server-side min(limit, 200) clamp)"
  - "GET /chat/state (pending_approvals + last 20 recent_messages, D-04)"
affects: [03-04, 03-05, 03-06, 03-07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pydantic model_validator(mode='after') for per-rule_type condition-shape validation, mirroring policy_engine._matches()'s exact field expectations, at the POST boundary (422 instead of deferred fail-closed DENY)"
    - "Unconditional min(limit, 200) clamp on every list endpoint accepting a client limit param — client can only lower, never raise, the cap"
    - "Shared _fetch_pending_approvals(session) helper reused by GET /approvals?status=pending and GET /chat/state — one query, two callers"

key-files:
  modified:
    - backend/main.py

key-decisions:
  - "GET /policies/rules has no explicit order_by: PolicyRule has no created_at column (unlike ApprovalRequest/ToolExecution/AuditLog), and D-08 groups rules by tool_name client-side anyway, so insertion-order-vs-recency was not load-bearing — documented inline with a ponytail comment rather than adding a schema migration for a cosmetic ordering requirement"
  - "list_approvals(status) reuses _fetch_pending_approvals for the PENDING branch (the one Task 3 also needs) and falls back to a small inline query for any other status value, avoiding a second near-duplicate dict-building block"

requirements-completed: [DASH-02, DASH-03, DASH-04, RT-02]

duration: 11min
completed: 2026-07-10
---

# Phase 3 Plan 2: Dashboard REST Read/Config Endpoints Summary

**Six additive route groups over existing `ToolCatalog`/`PolicyRule`/`ApprovalRequest`/`ToolExecution`/`AuditLog`/`Message` — no new discovery logic, no gateway bypass, no caching layer over policy rules.**

## Performance

- **Duration:** 11 min
- **Started:** 2026-07-10T12:38:00+05:30 (approx, context load)
- **Completed:** 2026-07-10T12:48:00+05:30
- **Tasks:** 3
- **Files modified:** 1 (backend/main.py)

## Accomplishments
- `GET /tools` returns `app.state.catalog.list_all_tools()` verbatim — no hardcoded tool names anywhere in the route (MCP-02 invariant preserved on the frontend side too).
- `PolicyRuleCreate` Pydantic model validates the condition shape per `rule_type` (`input_validation` needs string `prefix`; `token_budget` needs int `max_tokens`; `block_tool`/`require_approval` must have an empty condition) — a malformed rule now gets 422 at creation time instead of only failing, fail-closed, inside `evaluate()`'s catch-all.
- Policy-rule CRUD is toggle+delete+create only (D-07) — `PATCH /policies/rules/{id}` updates `enabled` exclusively; there is no route that edits `tool_name`/`condition`/`action`. No caching layer introduced anywhere — `load_rules()`'s fresh-read-per-call contract (POLICY-04) is untouched.
- `GET /approvals`, `GET /audit/executions`, `GET /audit/logs` all filter/order server-side; the two audit endpoints unconditionally clamp `limit` to `min(limit, 200)` regardless of what the client supplies (T-03-05 DoS mitigation), verified live with `limit=99999`.
- `GET /chat/state` returns `{pending_approvals, recent_messages}` for the current conversation — the Agent page's single reconnect re-fetch call (D-04). `recent_messages` is the last 20 rows, re-reversed to oldest→newest for transcript replay.
- Manually verified end-to-end via `TestClient(app)` for every route: valid rule create (200), malformed rule create (422 for both input_validation-missing-prefix and block_tool-with-condition), list/toggle/delete/re-delete (200/200/200/404), `/chat` + `/chat/state` round-trip showing both messages in correct order.

## Task Commits

Each task was committed atomically:

1. **Task 1: GET /tools + policy-rule CRUD with per-rule_type validation** - `7b8afa3` (feat)
2. **Task 2: Approvals list + audit list endpoints with server-side limit clamp** - `3574495` (feat)
3. **Task 3: GET /chat/state reconnect re-fetch endpoint** - `ba27039` (feat)

**Plan metadata:** (this commit, docs: complete plan)

## Response JSON Shapes (binding contract for 03-04/03-06/03-07 TypeScript types)

```
GET /tools -> list[dict]  # exact shape of ToolCatalog.list_all_tools() entries (MCP tool schema passthrough)

POST /policies/rules -> {"id": str}
GET /policies/rules -> list[{
  id: str, policy_id: str | None, rule_type: str, tool_name: str,
  condition: dict, action: "ALLOW" | "DENY" | "REQUIRE_APPROVAL", enabled: bool
}]
PATCH /policies/rules/{id} (body: {"enabled": bool}) -> {"ok": bool}   # 404 if rule missing
DELETE /policies/rules/{id} -> {"ok": bool}                            # 404 if rule missing

GET /approvals?status=PENDING -> list[{
  id: str, tool_name: str, arguments: dict, reason: str, status: str,
  decided_by: str | None, created_at: str (ISO), decided_at: str | None (ISO)
}]

GET /audit/executions?tool_name=&decision=&limit=200 -> list[{
  id: str, conversation_id: str | None, tool_name: str, arguments: dict,
  decision_action: str, decision_reason: str, matched_rule_ids: list[str],
  result_ok: bool | None, result_error: str | None, flagged_prompt_injection: bool,
  created_at: str (ISO)
}]

GET /audit/logs?event=&limit=200 -> list[{
  id: str, event: str, detail: dict, flags: str | None, created_at: str (ISO)
}]

GET /chat/state -> {
  pending_approvals: list[<same shape as GET /approvals PENDING row>],
  recent_messages: list[{role: str, content: str, created_at: str (ISO)}]  # oldest -> newest, max 20
}
```

## Files Created/Modified
- `backend/main.py` - added `Literal`-typed `PolicyRuleCreate`/`PolicyRuleToggle` Pydantic models with a `model_validator` condition-shape check, `_rule_to_dict`/`_approval_to_dict`/`_fetch_pending_approvals` helpers, and 8 new routes: `GET /tools`, `POST/GET/PATCH/DELETE /policies/rules[/{id}]`, `GET /approvals`, `GET /audit/executions`, `GET /audit/logs`, `GET /chat/state`. Imports extended: `HTTPException` (fastapi), `model_validator` (pydantic), `ApprovalRequest`/`AuditLog`/`ToolExecution` (models).

## Decisions Made
- No `order_by` on `GET /policies/rules` (no `created_at` column on `PolicyRule`; D-08's client-side tool_name grouping makes recency ordering non-load-bearing) — documented inline as a `ponytail:` comment rather than adding a schema migration.
- `list_approvals()`'s `PENDING` branch delegates to `_fetch_pending_approvals(session)`, the same helper `GET /chat/state` calls, per the plan's explicit "reuse the query" instruction for Task 3.

## Deviations from Plan

None - plan executed exactly as written. All acceptance criteria and verification commands passed as specified; no architectural changes, no new dependencies, no gateway/mcp_manager.call() touched.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- 03-04/03-06/03-07 (frontend Policies/Approvals/Audit/Agent pages) have the exact response JSON shapes above to build matching TypeScript types against.
- All six route groups are same-origin-reachable once the Vite proxy (03-04) is configured, per the existing `app = FastAPI(lifespan=lifespan)` instance — no new app/router object was created.

---
*Phase: 03-dashboard-real-time-polish*
*Completed: 2026-07-10*

## Self-Check: PASSED

- FOUND: backend/main.py
- FOUND: .planning/phases/03-dashboard-real-time-polish/03-02-SUMMARY.md
- FOUND commit: 7b8afa3
- FOUND commit: 3574495
- FOUND commit: ba27039
