---
phase: 02-persistence-approval-workflow
plan: 01
subsystem: database
tags: [fastapi, sqlalchemy, aiosqlite, sqlite, async, persistence, lifespan]

# Dependency graph
requires:
  - phase: 01-core-loop-terminal-verified
    provides: AgentLoop.run_turn(contents, conversation_id, token_usage), composition-root wiring (MCPManager/GeminiClient/ToolExecutionGateway/ToolCatalog), Settings/get_settings()
provides:
  - "backend/db.py: async engine, session factory, PRAGMA(foreign_keys/WAL) listener, init_models()"
  - "backend/models.py: Conversation, Message SQLAlchemy 2.0 declarative models"
  - "backend/main.py: FastAPI app (lifespan resume + POST /chat), REPL deleted"
  - "config.database_url setting"
affects: [02-02, 02-03, 02-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "FastAPI @asynccontextmanager lifespan (not deprecated @app.on_event) for startup/teardown"
    - "Eager select().order_by() inside one session scope for history replay (no lazy relationship traversal across async session boundary)"
    - "Module-level engine/async_session bound to get_settings().database_url at import time — tests override DATABASE_URL env var + get_settings.cache_clear() before first import"

key-files:
  created: [backend/db.py, backend/models.py, backend/test_main.py]
  modified: [backend/main.py, backend/config.py, backend/.gitignore]

key-decisions:
  - "Fake MCPManager/GeminiClient via monkeypatch in tests (not real subprocess/API) — Task 1 acceptance criterion 'no test hits real Gemini or MCP subprocess'"
  - "gitignore *.db/*.db-shm/*.db-wal — runtime SQLite files must never be tracked"
  - "Persist only the completed turn (user + final assistant text); mid-turn tool-call crash recovery explicitly out of scope this phase (ponytail note, D-02)"

patterns-established:
  - "Pattern: db.py module-level engine binds to get_settings().database_url at import time — any test suite touching db/main must set DATABASE_URL env var + get_settings.cache_clear() before the first import"

requirements-completed: [AGENT-03, DB-01]

# Metrics
duration: ~20min
completed: 2026-07-10
---

# Phase 02 Plan 01: Persistence Foundation + FastAPI Chat Summary

**Converted the Phase 1 terminal REPL into a FastAPI app whose `POST /chat` drives `AgentLoop.run_turn()` and persists each completed turn to SQLite via async SQLAlchemy, with eager history replay + a logged resume line on restart.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3 completed
- **Files modified:** 6 (backend/db.py, backend/models.py, backend/config.py, backend/main.py, backend/test_main.py, backend/.gitignore)

## Accomplishments
- `POST /chat` drives a real agent turn and returns `{"final_text": ...}` synchronously (D-01, D-02)
- Each turn's user + assistant messages are durable in SQLite via `AsyncSession`/`aiosqlite` (DB-01)
- FastAPI lifespan eager-loads full conversation history at startup and logs `Resumed conversation {id}: {N} prior messages loaded` (D-08, D-09, AGENT-03 Success Criterion 1)
- Phase 1 terminal REPL (`input()` loop, `_CONVERSATION_ID` constant) deleted outright, not kept as a wrapper (D-03)

## Task Commits

Each task was committed atomically:

1. **Task 1: Failing end-to-end test for chat persistence + restart resume** - `930576d` (test)
2. **Task 2: DB infra + Conversation/Message models + config field** - `9212cc6` (feat)
3. **Task 3: FastAPI app — lifespan resume + POST /chat persistence** - `6352631` (feat)

Plus one small follow-up documenting a threat-model disposition:
4. **T-02-02 ponytail note** - `2cc6c95` (docs)

**Plan metadata:** (this commit) - `docs: complete plan`

_TDD task 1 is RED-only in this plan — GREEN lands via Task 2+3's feat commits (db.py/models.py/main.py didn't exist yet when Task 1 committed, so the RED failure was an `AttributeError: module 'main' has no attribute 'app'`, not a plain import error, since `main.py`'s existing imports at the time were all valid Phase-1 modules)._

## Files Created/Modified
- `backend/db.py` - async engine, session factory, `PRAGMA foreign_keys=ON`/`journal_mode=WAL` connect listener, `init_models()`
- `backend/models.py` - `Conversation`, `Message` SQLAlchemy 2.0 `Mapped[...]` declarative models, FK, indexed `created_at`
- `backend/config.py` - added `database_url: str = "sqlite+aiosqlite:///./guarded_agent.db"`
- `backend/main.py` - FastAPI app: `lifespan` (init_models, composition-root wiring, eager conversation load/create, resume log), `POST /chat` (persist user + assistant messages, return final_text)
- `backend/test_main.py` - 3 tests: persistence, restart-reload (resume log regex + seeded contents), response body
- `backend/.gitignore` - added `*.db`/`*.db-shm`/`*.db-wal` (runtime SQLite output)

## Decisions Made
- **Fake MCPManager/GeminiClient in tests, not real ones:** Task 1's acceptance criteria explicitly forbid hitting real Gemini or a real MCP subprocess. Since `db.py` binds its engine to `get_settings().database_url` at import time, the test fixture sets `GEMINI_API_KEY`/`DATABASE_URL` env vars and calls `get_settings.cache_clear()` before the first `import main`, then monkeypatches `main.MCPManager`/`main.GeminiClient` to no-op fakes, and finally swaps `app.state.agent_loop` for a `FakeAgentLoop` stub after lifespan startup — keeping the real composition-root wiring code path exercised (catalog/gateway/agent_loop construction) while never touching real infrastructure.
- **gitignore runtime `.db` files:** Task 2's verify command (`uv run python -c "...init_models()..."`) creates a real `guarded_agent.db` (+ WAL/SHM sidecar files) in `backend/`. These are generated runtime output, not source — added to `backend/.gitignore` and removed from the working tree (Rule 2: missing critical functionality — untracked generated files must never ship).
- **T-02-02 ponytail note:** the threat model's `accept` disposition for unbounded-history-at-startup explicitly calls for a `# ponytail:` note; added one directly on `_load_or_create_conversation()` documenting the accepted ceiling and the future upgrade path (pagination/truncation).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] gitignore runtime SQLite files**
- **Found during:** Task 2 (DB infra verify command)
- **Issue:** Running the plan's own verify command created `guarded_agent.db`, `guarded_agent.db-shm`, `guarded_agent.db-wal` in `backend/`, which were untracked and about to be left in the working tree
- **Fix:** Added `*.db`, `*.db-shm`, `*.db-wal` to `backend/.gitignore`; deleted the generated files
- **Files modified:** `backend/.gitignore`
- **Verification:** `git status --short` shows no untracked `.db*` files after cleanup
- **Committed in:** `9212cc6` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical - gitignore hygiene)
**Impact on plan:** No scope creep; purely a repo-hygiene fix required for a clean working tree.

## Issues Encountered
- This worktree's `.planning/` directory is entirely gitignored in the main repo (only `PROJECT.md` and `*-SUMMARY.md` files are tracked via explicit `git add`), so `02-01-PLAN.md`, `STATE.md`, `ROADMAP.md`, `02-CONTEXT.md`, `02-RESEARCH.md`, and `02-PATTERNS.md` were not present in this git worktree. Read directly from the main repo's absolute path (`D:\Docs\Computer\projects\guarded-ai\.planning\...`) instead — same content, just outside the worktree's tracked-file boundary. No functional impact; documented here for the orchestrator's awareness since this pattern will recur for every worktree-isolated plan in this project.
- No `backend/.env` exists in this worktree (gitignored, main-repo-only). `GEMINI_API_KEY` is a required `Settings` field with no default, so the Task 2 verify command needed an inline env var override (`GEMINI_API_KEY=dummy-key uv run python -c ...`); the actual test suite sets this via `monkeypatch.setenv` internally and needs no external override.

## User Setup Required

None - no external service configuration required. (A real `backend/.env` with a live `GEMINI_API_KEY` is still needed for the plan's own manual `<verification>` smoke test — `uv run uvicorn main:app` + curl — but that was not run this session since it requires a real Gemini API key not present in this worktree; the automated `pytest test_main.py` path, which is what Task 3's acceptance criteria actually require, is fully green.)

## Next Phase Readiness
- `Conversation`/`Message` persistence and the FastAPI app skeleton (`lifespan`, `app.state.agent_loop`/`contents`/`conversation_id`) are in place for Plan 02-02 (policy rules → DB, `POST /approvals/{id}`, `ApprovalManager`) to build on directly — `gateway.py`'s constructor signature and `policy_engine.load_rules()` are UNCHANGED this plan, exactly as scoped.
- No blockers. The live-server manual verification (`uvicorn` + curl with a real Gemini key) is deferred to whenever a human runs it with real credentials — not a gate on this plan's completion since Task 3's automated acceptance criteria (all covering the actual `must_haves`) are fully satisfied.

---
*Phase: 02-persistence-approval-workflow*
*Completed: 2026-07-10*

## Self-Check: PASSED

All created files verified present (backend/db.py, backend/models.py, backend/test_main.py, SUMMARY.md) and all 5 commit hashes (930576d, 9212cc6, 6352631, 2cc6c95, 5764afc) verified in git log.
