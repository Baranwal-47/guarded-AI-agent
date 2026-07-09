---
phase: 02-persistence-approval-workflow
plan: 02
subsystem: database
tags: [sqlalchemy, sqlite, policy-engine, fastapi]

# Dependency graph
requires:
  - phase: 02-01
    provides: backend/db.py (async engine + session factory), backend/models.py (Conversation, Message), backend/main.py FastAPI lifespan skeleton
provides:
  - PolicyRule + Policy ORM models (backend/models.py)
  - async, session-based, fresh-read-per-call policy_engine.load_rules
  - gateway.py wired to a DB session factory instead of a YAML path
  - startup seed of the 9 policy_rules.yaml rules into the policy_rules table (idempotent)
  - backend/conftest.py fixing a test-collection-order DB-binding bug
affects: [02-03, 02-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "load_rules(session) reads PolicyRule fresh via select() on every gateway.execute_tool call — never cached, no lru_cache/module-level list"
    - "gateway.py opens one fresh AsyncSession per execute_tool call (async with self.session_factory() as session)"
    - "conftest.py sets GEMINI_API_KEY/DATABASE_URL before any test module collects, since db.py binds its global engine at first import"

key-files:
  created: [backend/test_policy_db.py, backend/conftest.py]
  modified: [backend/models.py, backend/policy_engine.py, backend/gateway.py, backend/main.py, backend/test_gateway.py, backend/tests/test_policy_engine.py]

key-decisions:
  - "PolicyRule columns line up 1:1 with policy_engine.Rule (id, rule_type, tool_name, condition JSON, action str, enabled bool) so load_rules() converts a row straight into a Rule dataclass with zero extra mapping"
  - "evaluate(), _matches(), _PRECEDENCE, and all three dataclasses in policy_engine.py are byte-for-byte unchanged — only load_rules()'s body/signature changed (async, session param instead of path)"
  - "Startup seed is a plain idempotent count-then-insert-if-empty step in main.py's lifespan, not a migration tool (no Alembic per CLAUDE.md)"

patterns-established:
  - "Any new DB-backed policy/state read follows load_rules' shape: async def fn(session) -> ..., select() every call, no memoization"

requirements-completed: [POLICY-04, DB-01]

# Metrics
duration: 30min
completed: 2026-07-10
---

# Phase 02 Plan 02: DB-Sourced Fresh-Read Policy Rules Summary

**Policy rules moved from `policy_rules.yaml` into a `policy_rules` SQLite table, read fresh via `async load_rules(session)` on every gateway call with zero caching — editing a rule row changes the very next matching tool call's decision with no backend restart.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-07-10T00:38:00+05:30 (approx, first task commit 00:40:24)
- **Completed:** 2026-07-10T00:50:03+05:30
- **Tasks:** 3
- **Files modified:** 8 (2 new: test_policy_db.py, conftest.py; 6 modified)

## Accomplishments
- `Policy`/`PolicyRule` SQLAlchemy models added, columns matching `policy_engine.Rule` 1:1
- `policy_engine.load_rules` is now `async def load_rules(session) -> list[Rule]`, reading `PolicyRule` rows fresh via `select()` every call — no `@lru_cache`, no module cache
- `gateway.py` constructor takes a `session_factory`; `execute_tool` opens one fresh `AsyncSession` per call before evaluating, preserving the existing fail-closed exception path
- `main.py` lifespan seeds the `policy_rules` table from `policy_rules.yaml` on first startup (idempotent — verified via manual smoke test: 9 rows seeded, second call is a no-op) and wires the gateway to `async_session` instead of `settings.policy_rules_path`
- `evaluate()`, `_matches()`, `_PRECEDENCE`, and all three dataclasses in `policy_engine.py` are unchanged (verified via `git diff`)

## Task Commits

Each task was committed atomically:

1. **Task 1: Failing tests — DB-sourced fresh-read rules + live edit changes decision** - `a426bee` (test)
2. **Task 2: PolicyRule model + async DB load_rules + gateway session wiring** - `b2c3971` (feat)
3. **Task 3: Startup rule seed + adapt gateway tests to session factory** - `de7ac49` (feat)

_Note: this plan's tasks were not individually TDD-gated per-task (`tdd="true"` only on Task 1); Task 1 established the RED tests, Tasks 2-3 turned them GREEN._

## Files Created/Modified
- `backend/models.py` - Added `Policy` and `PolicyRule` ORM models
- `backend/policy_engine.py` - `load_rules` is now async and DB-sourced (session param, `select(PolicyRule)`); `evaluate`/`_matches`/`_PRECEDENCE`/dataclasses untouched
- `backend/gateway.py` - Constructor takes `session_factory`; `execute_tool` opens a fresh session per call before `load_rules`
- `backend/main.py` - Added `_seed_policy_rules_if_empty()` seed step in lifespan; gateway now constructed with `async_session`
- `backend/test_policy_db.py` (new) - 3 tests: fresh-read conversion, live-edit-no-cache (via two conflicting DENY/ALLOW rules), async signature check
- `backend/test_gateway.py` - `rules_path` YAML fixture replaced by `session_factory` fixture seeded with `PolicyRule` rows; all pre-existing assertions preserved
- `backend/tests/test_policy_engine.py` - Updated the one Phase-1 test calling the old sync `load_rules(path)` to seed a throwaway DB and call the new async signature
- `backend/conftest.py` (new) - Sets `GEMINI_API_KEY`/`DATABASE_URL` before test collection (see Deviations)

## Decisions Made
- Used two conflicting rules (DENY + ALLOW, both `require_approval` rule_type so both always match on tool_name) in `test_edited_rule_changes_next_evaluation_no_cache` rather than a single rule, because disabling the only matching rule for a tool falls into `evaluate()`'s own no-match fail-closed DENY default — the same DENY outcome either way, which wouldn't actually distinguish stale-cache from fresh-read. Two conflicting rules mirror the plan's own `policy_rules.yaml` R01/R03 pattern and prove freshness unambiguously (ALLOW surfaces once DENY is disabled).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test-collection-order DB-binding bug writing to the real dev database**
- **Found during:** Task 3 (running `uv run pytest` for the full suite after adapting test_gateway.py)
- **Issue:** `db.py` binds its module-level `engine`/`async_session` to `get_settings().database_url` at *first import*. Before this plan, `test_gateway.py` never imported `db` (rules were YAML-based), so `test_main.py`'s fixture — which sets `DATABASE_URL` to a throwaway `tmp_path` file before its own `import main` — was always the first to trigger `db`'s import. This plan's Task 2 makes `policy_engine.py` import `models` (→ `db`) at module level, so `test_gateway.py`/`test_policy_db.py`/`tests/test_policy_engine.py` (all collected before `test_main.py`) now trigger `db`'s first import during pytest *collection*, before any env var is set — binding the global engine to its real default `./guarded_agent.db`. Running the full suite silently created and wrote to that real file (verified: `guarded_agent.db`/`-shm`/`-wal` appeared in `backend/` after a full run, and `test_main.py`'s history-count assertions failed because they were reading accumulated cross-test state from that same file).
- **Fix:** Added `backend/conftest.py`, which pytest imports before any test module is collected, setting `GEMINI_API_KEY`/`DATABASE_URL` (to a fresh `tempfile.mkdtemp()` location) as env-var defaults. This guarantees `db.py`'s engine binds to a safe throwaway file no matter which test module happens to import it first — a single root-cause fix instead of scattering `os.environ.setdefault` calls across every test file that transitively imports `db`/`models`.
- **Files modified:** `backend/conftest.py` (new)
- **Verification:** Ran `uv run pytest` twice in a row — 31/31 passed both times, no `.db`/`.db-shm`/`.db-wal` files left in `backend/` after either run.
- **Committed in:** `de7ac49` (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary to keep the test suite from silently mutating the real dev SQLite file; no scope creep — confined to test infrastructure, no production code path affected.

## Issues Encountered
- Initial `test_edited_rule_changes_next_evaluation_no_cache` design (single DENY rule, disable it, expect non-DENY) was wrong: `evaluate()`'s own no-match fail-closed default is also DENY, so the test couldn't actually distinguish stale-cache from fresh-read. Redesigned with two conflicting rules before committing Task 2 (see Decisions Made above) — never landed the flawed version in a passing state.

## User Setup Required

None - no external service configuration required. The plan's manual `<verification>` smoke test (start uvicorn, `/chat` with a DENY-matching tool call, edit the DB row, re-issue with no restart) was not run this session since it requires a real `GEMINI_API_KEY` not present in this worktree (same situation noted in 02-01-SUMMARY.md) — the automated `pytest test_gateway.py test_policy_db.py` path, which is what this plan's acceptance criteria actually require, is fully green (31/31 across the whole backend suite). The seed step's idempotency and correctness (9 rows seeded, no duplication on restart) was verified via a standalone manual script run against a throwaway SQLite file.

## Next Phase Readiness
- Policy rules are DB-backed and fresh-read on every gateway call — ready for a dashboard rule-editing UI (Phase 3) to change agent behavior live with zero backend restart.
- `gateway.py`'s constructor now takes `(mcp_manager, session_factory)`; Plan 03 (approval workflow) will extend this constructor further (adding `approval_manager`) per 02-PATTERNS.md — no rework needed on the `session_factory` piece.
- `backend/conftest.py` now exists as the canonical place to add further test-session-wide env var defaults if Plan 03/04 introduce new DB-backed test modules.

---
*Phase: 02-persistence-approval-workflow*
*Completed: 2026-07-10*

## Self-Check: PASSED

All created/modified files verified present on disk (backend/test_policy_db.py, backend/conftest.py, backend/models.py, backend/policy_engine.py, backend/gateway.py, backend/main.py, backend/test_gateway.py, backend/tests/test_policy_engine.py, this SUMMARY.md). All 3 task commit hashes (a426bee, b2c3971, de7ac49) verified present in git log. Full backend test suite (`uv run pytest`) passes 31/31, run twice consecutively with no leftover db artifacts.
