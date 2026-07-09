---
phase: 01-core-loop-terminal-verified
plan: 02
subsystem: policy-engine
tags: [pydantic-settings, pyyaml, pytest, policy-engine, dataclasses]

# Dependency graph
requires: []
provides:
  - "backend/ package scaffold (pyproject.toml, uv-managed, package=false)"
  - "backend/config.py: pydantic-settings Settings + get_settings()"
  - "backend/policy_rules.yaml: 9 seed rules across all 4 rule types with deliberate delete_file conflict"
  - "backend/policy_engine.py: Action/PolicyContext/PolicyDecision/Rule + load_rules() + evaluate()"
  - "backend/tests/test_policy_engine.py: 12 passing tests"
affects: ["01-04 (gateway consumes evaluate()/PolicyContext as-is)", "phase-2 (swaps load_rules() DB source only)"]

# Tech tracking
tech-stack:
  added: [pydantic-settings, pyyaml, pytest, mcp, google-genai, fastapi, uvicorn, sqlalchemy, aiosqlite, websockets]
  patterns:
    - "Gather-all-then-reduce precedence: collect every matching (rule_id, action) pair into a set, pick winner via a fixed _PRECEDENCE tuple — never first-match"
    - "Fail-closed by construction: empty match set AND per-rule match exceptions both fall into the same 'matched, DENY' bucket, so evaluate() never raises and never implicitly ALLOWs"
    - "Rule shape mirrors future DB row (id, rule_type, tool_name, condition, action, enabled) so Phase 2 swaps load_rules() only"

key-files:
  created:
    - backend/pyproject.toml
    - backend/.env.example
    - backend/.gitignore
    - backend/config.py
    - backend/policy_rules.yaml
    - backend/policy_engine.py
    - backend/tests/test_policy_engine.py
  modified: []

key-decisions:
  - "pytest pythonpath=[\".\"] added to pyproject.toml (not in original plan) so tests import policy_engine.py from the backend root without needing a package/src layout"
  - "input_validation condition carries an explicit 'arg' key (e.g. {prefix: \"reports/\", arg: \"path\"}) since the plan's condition shape didn't specify which argument name to check per tool"
  - "matched_rule_ids includes every rule that matched regardless of which action ultimately won (not just the winning action's rules) — required so the delete_file conflict test can see both R01 and R03"

patterns-established:
  - "Pattern 1: Policy Engine is a pure module — zero I/O beyond load_rules() reading the YAML path it's given; no MCP, DB, or print() imports"
  - "Pattern 2: TDD RED/GREEN gate — test(01-02) commit (ModuleNotFoundError, confirmed failing) before feat(01-02) commit (12/12 passing)"

requirements-completed: [POLICY-01, POLICY-02, POLICY-03, POLICY-05]

# Metrics
duration: ~15min
completed: 2026-07-09
---

# Phase 1 Plan 2: Backend Scaffold + Policy Engine Summary

**Pure, fully-tested Policy Engine (`evaluate()`/`load_rules()`) with gather-all-then-reduce DENY>REQUIRE_APPROVAL>ALLOW precedence over a 9-rule YAML seed set, plus the backend package scaffold and pydantic-settings config.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-09
- **Tasks:** 2/2 completed
- **Files modified:** 7 created

## Accomplishments

- `backend/policy_engine.py`: `Action` enum, frozen `PolicyContext`/`PolicyDecision`/`Rule` dataclasses, `load_rules()` (fresh YAML read, no cache), `evaluate()` (gather-all-then-reduce, fail-closed on empty match or per-rule exception)
- `backend/policy_rules.yaml`: 9 seed rules covering all 4 rule types (`block_tool`, `require_approval`, `input_validation`, `token_budget`) across both MCP servers' tools, with a deliberate `delete_file` conflict (R01 `block_tool`→DENY vs R03 `require_approval`→REQUIRE_APPROVAL) proving precedence resolves to DENY
- `backend/config.py`: `Settings`/`get_settings()` via `pydantic-settings`, `gemini_model` defaults `gemini-2.5-flash`, `max_agent_steps` defaults `10`
- `backend/tests/test_policy_engine.py`: 12 tests covering every `<behavior>` bullet — precedence conflict + reversed-order twin, require_approval-only, input_validation violation/compliance, token_budget over/under, fail-closed on zero match, structured-args-only `PolicyContext`, malformed-condition fail-closed, disabled-rule exclusion, and `load_rules()` against the real seed YAML

## Task Commits

Each task was committed atomically:

1. **Task 1: Backend scaffold, config, seeded policy_rules.yaml** - `6e60b46` (feat)
2. **Task 2: Policy Engine evaluate() with tests** - `d1dff8d` (test, RED — ModuleNotFoundError) then `114d598` (feat, GREEN — 12/12 passing)

_TDD task: RED (`d1dff8d`) confirmed failing via ModuleNotFoundError before GREEN (`114d598`) implemented policy_engine.py. No REFACTOR commit needed — implementation was clean on first pass._

## Files Created/Modified

- `backend/pyproject.toml` - backend deps (mcp, google-genai, fastapi, uvicorn, sqlalchemy, aiosqlite, pydantic-settings, websockets, pyyaml) + pytest dev group + `pythonpath=["."]` pytest config
- `backend/.env.example` - documents `GEMINI_API_KEY`/`CONTEXT7_API_KEY`
- `backend/.gitignore` - excludes `.venv/`, `__pycache__/`, `.env` (not in original plan's `files_modified` — see Deviations)
- `backend/config.py` - `Settings`/`get_settings()` (pydantic-settings)
- `backend/policy_rules.yaml` - 9-rule seed set, deliberate `delete_file` conflict
- `backend/policy_engine.py` - `Action`, `PolicyContext`, `PolicyDecision`, `Rule`, `load_rules()`, `evaluate()`
- `backend/tests/test_policy_engine.py` - 12 tests, all passing
- `backend/uv.lock` - generated by `uv sync`

## Decisions Made

- Added `[tool.pytest.ini_options] pythonpath = ["."]` to `pyproject.toml` — pytest's default "prepend" import mode only adds the test file's own directory (`tests/`) to `sys.path`, not the parent `backend/` root where `policy_engine.py`/`config.py` live. Without this, `import policy_engine` fails even though `policy_engine.py` exists. Small, standard pytest config addition, not a structural change.
- `input_validation` rule `condition` carries an explicit `arg` key (defaulting to `"path"` if omitted) so `_matches()` knows which argument to prefix-check — the plan named `condition['prefix']` but didn't specify how the checked argument name is determined per tool.
- `matched_rule_ids` in `PolicyDecision` includes every rule that matched at all (not just rules sharing the winning action) — required by the plan's own conflict-case behavior spec ("BOTH rule ids in matched_rule_ids").

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added `backend/.gitignore` and `pytest pythonpath` config**
- **Found during:** Task 1 (uv sync created `.venv/`/`__pycache__/`) and Task 2 (pytest couldn't import `policy_engine`)
- **Issue:** `uv sync` generates `.venv/` and `__pycache__/` which would otherwise get force-committed; pytest's default import mode couldn't find `policy_engine.py` outside `tests/`
- **Fix:** Added `backend/.gitignore` (`.venv/`, `__pycache__/`, `*.pyc`, `.env`); added `[tool.pytest.ini_options] pythonpath = ["."]` to `pyproject.toml`
- **Files modified:** `backend/.gitignore` (new), `backend/pyproject.toml`
- **Verification:** `git status --short` shows no stray `.venv`/`__pycache__` entries; `uv run pytest tests/test_policy_engine.py -q` finds and imports `policy_engine` cleanly
- **Committed in:** `6e60b46` (`.gitignore`), `114d598` (pytest config)

**2. [Rule 2 - Missing Critical] `.planning/` is gitignored at repo root but this worktree's SUMMARY.md must persist**
- **Found during:** Post-execution (this summary)
- **Issue:** Repo-level `.gitignore` excludes `.planning/` ("kept local-only" per prior commit), but the orchestrator's worktree protocol requires this SUMMARY.md to be committed and force-removes the worktree afterward — an uncommitted file here would be permanently lost
- **Fix:** Force-added this SUMMARY.md with `git add -f` despite the repo-wide `.planning/` ignore rule, per the explicit worktree-execution instruction that overrides the general `commit_docs: false` config for this specific handoff artifact
- **Files modified:** `.planning/phases/01-core-loop-terminal-verified/01-02-SUMMARY.md`
- **Verification:** `git show HEAD --stat` includes this file after commit
- **Committed in:** (see final commit below)

---

**Total deviations:** 2 auto-fixed (2 blocking/critical-persistence). No scope creep — both are infra necessities, not feature additions.
**Impact on plan:** None on the Policy Engine's design or interface; both fixes are tooling/process only.

## Issues Encountered

None beyond the two auto-fixes above.

## User Setup Required

None - no external service configuration required. (`GEMINI_API_KEY` is documented in `.env.example` but not needed until Phase 1 Plan 3+ wires the agent loop.)

## Next Phase Readiness

- `evaluate()`/`PolicyContext`/`PolicyDecision` public surface is stable and ready for `01-04`'s Gateway to consume directly — no interface changes anticipated for Phase 2's DB-backed rule swap (only `load_rules()`'s implementation changes).
- `policy_rules.yaml`'s rule shape (`id`, `rule_type`, `tool_name`, `condition`, `action`, `enabled`) is DB-row-shaped per D-04, minimizing Phase 2 rework.
- No blockers for `01-01` (Sandbox File Manager, independent) or `01-03`/`01-04` (MCP Manager / Gateway, which will import `policy_engine.evaluate`).

---
*Phase: 01-core-loop-terminal-verified*
*Completed: 2026-07-09*

## Self-Check: PASSED

All 7 created files verified present on disk; all 3 task commits (`6e60b46`, `d1dff8d`, `114d598`) verified in `git log`.
