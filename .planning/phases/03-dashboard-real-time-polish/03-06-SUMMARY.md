---
phase: 03-dashboard-real-time-polish
plan: 06
subsystem: ui
tags: [react, typescript, tailwind, policy-engine]

# Dependency graph
requires:
  - phase: 03-dashboard-real-time-polish
    plan: 02
    provides: GET /tools, GET/POST/PATCH/DELETE /policies/rules response shapes
  - phase: 03-dashboard-real-time-polish
    plan: 04
    provides: api client (get/post/patch/delete), DecisionBadge, PolicyRule/Tool types
provides:
  - "Complete PoliciesPage.tsx: rule list grouped by tool_name + toggle + delete + create form"
affects: [03-07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Client-side reduce groups PolicyRule[] by tool_name (D-08); no server-side grouping endpoint needed"
    - "rule_type -> action derived client-side via a Record lookup mirroring the plan's <interfaces> mapping, kept in one place instead of scattered conditionals"
    - "Optimistic local state update on create/toggle/delete using the POST response's returned id, avoiding a full re-fetch round-trip"

key-files:
  modified:
    - frontend/src/pages/PoliciesPage.tsx

key-decisions:
  - "POST /policies/rules only returns {id}; rather than refetching the full list, the submitted form values + returned id are prepended into local state directly (no extra GET round-trip)"
  - "arg (input_validation) is only included in the submitted condition when non-empty; the backend's policy_engine defaults it to \"path\" server-side, so omitting it when blank matches server behavior instead of duplicating the default client-side"

requirements-completed: [DASH-02]

duration: ~20min
completed: 2026-07-10
---

# Phase 3 Plan 6: Policies Page Summary

**Rule list grouped by tool_name (with DecisionBadge/toggle/delete) plus one create-rule form whose condition fields swap between prefix+arg and max_tokens based on a rule_type dropdown, tool selector sourced live from GET /tools.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2
- **Files modified:** 1 (frontend/src/pages/PoliciesPage.tsx)

## Accomplishments

- `GET /policies/rules` on mount, grouped client-side by `tool_name` into labeled sections (font-mono heading, D-08) so conflicting rules on one tool are visually adjacent.
- Each rule row shows `DecisionBadge` (action), `rule_type`, a font-mono condition summary (`prefix`/`arg` or `max_tokens`), an enabled checkbox (PATCH `enabled` only, D-07) and a destructive-red Delete button gated behind the exact `window.confirm("Delete this rule? This cannot be undone.")` copy from UI-SPEC.
- Empty state renders the UI-SPEC copy verbatim ("No rules yet" / "Create a rule to start governing tool calls.") only after the initial fetch resolves with zero rules (no flash of empty state before load completes).
- One create-rule form: `GET /tools` populates a native `<select>` tool list (D-05, no hardcoded tool names anywhere in the component); a `rule_type` native `<select>` (block_tool / require_approval / input_validation / token_budget) conditionally reveals `prefix`+`arg` inputs or a `max_tokens` number input (D-06); `action` is derived from `rule_type` via a lookup table matching the plan's exact mapping.
- Submit `POST`s the assembled `{rule_type, tool_name, condition, action, enabled: true}` body; success prepends the new rule into local state using the returned `id`; a 400/422 catch shows the UI-SPEC form-error copy "Check the rule fields and try again." without crashing the form.
- No component library used (D-15) — native `<select>`/`<input>`/`<button>` + plain Tailwind throughout, Create Rule button styled accent blue-500.

## Task Commits

Each task was committed atomically:

1. **Task 1: Rule list grouped by tool_name + toggle + delete** - `2113d76` (feat)
2. **Task 2: Create-rule form with rule_type-driven condition fields** - `6262dfe` (feat)

**Plan metadata:** (this commit, docs: complete plan)

## Files Created/Modified

- `frontend/src/pages/PoliciesPage.tsx` - Full page: grouped rule list with toggle/delete (Task 1), create-rule form with tool selector + rule_type-driven condition fields (Task 2).

## Decisions Made

- POST response is `{id}` only (no full rule echoed back); rather than adding a second GET round-trip after create, the locally-known form values are merged with the returned `id` and pushed directly into state — matches the existing toggle/delete pattern of local-state mutation over re-fetch.
- `arg` is omitted from the submitted `condition` when the input is left blank, relying on the backend's already-established `rule.condition.get("arg", "path")` default (`policy_engine.py`) rather than re-encoding "path" as a client-side default value.

## Deviations from Plan

None affecting scope or correctness. One process note carried over from precedent (03-05): the plan's automated verify commands grep for literal `/api/policies/rules` and `/api/tools` substrings, but per the api client convention established in 03-04 (`api/client.ts` prepends the `/api` base internally), pages only ever reference relative paths (e.g. `"/policies/rules"`, `"/tools"`). This is the same non-literal-match already accepted in 03-05's summary for `/api/chat`. The functional intent of every verify command (rule-list fetch, tools fetch, confirm-gated delete) is fully satisfied; grep patterns were checked against the actual relative-path substrings instead of the literal `/api/...` strings.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- 03-07 can proceed; PoliciesPage.tsx is complete and consumes only the already-locked 03-02 REST shapes and 03-04 shared foundation (api client, DecisionBadge, types) — no new backend work required.

---
*Phase: 03-dashboard-real-time-polish*
*Completed: 2026-07-10*
