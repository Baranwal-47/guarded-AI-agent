---
phase: 01-core-loop-terminal-verified
plan: 04
subsystem: agent-loop
tags: [google-genai, gemini, function-calling, policy-gateway, pytest]

# Dependency graph
requires:
  - phase: 01-02
    provides: "policy_engine.evaluate()/load_rules()/PolicyContext/PolicyDecision/Action, consumed as-is"
  - phase: 01-03
    provides: "MCPManager.call()/list_all_tools(), ToolResult dataclass, consumed as-is"
provides:
  - "backend/gemini_client.py: GeminiClient - build_tools() from live schemas, generate() with automatic_function_calling disabled, function_calls/text/function_response_part helpers"
  - "backend/gateway.py: ToolExecutionGateway.execute_tool() - single policy-gated MCP call choke point, uniform ToolResult return"
  - "backend/test_gateway.py: 8 passing tests (ALLOW/DENY/approval-y/approval-n/approval-empty/policy-exception/uniform-return-type/signature)"
affects: ["01-05 (agent loop wires GeminiClient.generate() output into gateway.execute_tool(), builds the ReAct loop)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Gateway is the sole call site of MCPManager.call() - grep-verified single match in backend/"
    - "Every gateway branch (ALLOW/DENY/approval-rejected/approval-approved/policy-exception) returns the same ToolResult class imported from mcp_manager, never a locally-declared shape"
    - "Async tests via plain asyncio.run() instead of pytest-asyncio - stdlib already covers running the one async method under test, no new dev dependency"

key-files:
  created:
    - backend/gemini_client.py
    - backend/gateway.py
    - backend/test_gateway.py
  modified: []

key-decisions:
  - "Used asyncio.run() in tests instead of adding pytest-asyncio - the plan's fakes never need a live event loop beyond what execute_tool() itself starts, so no dependency was needed (ponytail: stdlib solves it)"
  - "Docstrings deliberately avoid the literal string 'ClientSession' anywhere in gemini_client.py (even in comments) since the plan's automated verify greps the whole file text, not just imports"

patterns-established:
  - "Pattern: Gateway builds PolicyContext from structured (tool_name, server_name, arguments, conversation_id, token_usage) only - no reasoning/intent/free-text parameter exists on execute_tool(), enforced by a signature test"
  - "Pattern: policy engine exceptions and non-affirmative approval input both fail closed to a synthesized ToolResult(ok=False, ...) - never propagate, never implicit ALLOW"

requirements-completed: [AGENT-02]

# Metrics
duration: ~20min
completed: 2026-07-09
---

# Phase 1 Plan 4: Gemini Client + Tool Execution Gateway Summary

**GeminiClient with automatic function calling hard-disabled on every call, and a ToolExecutionGateway that is the sole caller of MCPManager.call(), gating every tool request through the Policy Engine and returning one uniform ToolResult shape across ALLOW/DENY/approval/fail-closed branches.**

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-09
- **Tasks:** 2/2 completed
- **Files modified:** 3 created

## Accomplishments

- `backend/gemini_client.py`: `GeminiClient(api_key, model)` wraps `genai.Client`; `build_tools()` maps live `list_all_tools()` entries to `FunctionDeclaration(parameters_json_schema=...)` (never hardcoded tool names), wrapped in one `Tool`; `generate()` always sets `automatic_function_calling=AutomaticFunctionCallingConfig(disable=True)` (AGENT-02); helper methods expose `response.function_calls`/`response.text` and build a `function_response` `Part` for feeding results back next turn. Module never executes a tool.
- `backend/gateway.py`: `ToolExecutionGateway(mcp_manager, rules_path).execute_tool(...)` builds `PolicyContext` from structured args only, calls `load_rules()` fresh (no cache) + `evaluate()` inside a try/except that fails closed to DENY on any policy exception, prints `[POLICY]`/`[APPROVAL]`/`[RESULT]` log lines, and branches: DENY → synthesized `ToolResult(ok=False,...)` with zero MCP calls; REQUIRE_APPROVAL → synchronous `input("Approve...? [y/N]: ")`, only exact `y`/`yes` (case-insensitive, stripped) proceeds, anything else fails closed; ALLOW or approved → the one and only `self.mcp_manager.call(...)` call site in the module. Every branch returns the same `ToolResult` class imported from `mcp_manager`.
- `backend/test_gateway.py`: 8 tests using a fake MCP manager + temp YAML rules files — DENY blocks with zero calls, approval rejected/empty both fail closed, approval "y" executes, a genuine ALLOW-rule tool call executes, a monkeypatched `evaluate()` exception fails closed, DENY/ALLOW results share the exact same `ToolResult` class, and `execute_tool`'s signature has no `reasoning`/`intent`/`llm_text`-style parameter.

## Task Commits

Each task was committed atomically:

1. **Task 1: Gemini client — FunctionDeclarations from live schemas, AFC disabled** - `d5c6ab7` (feat)
2. **Task 2: Tool Execution Gateway — policy-gated choke point + tests** - `323bfcd` (feat)

_Neither task had `tdd="true"` in the plan frontmatter; tests were written alongside the implementation in the single Task 2 commit per the plan's own instructions._

## Files Created/Modified

- `backend/gemini_client.py` - `GeminiClient`: `build_tools()`, `generate()` (AFC always disabled), `function_calls()`/`text()`/`function_response_part()` helpers
- `backend/gateway.py` - `ToolExecutionGateway`: `execute_tool()`, the sole `MCPManager.call()` call site in the codebase
- `backend/test_gateway.py` - 8 tests covering every branch + the uniform-return-type and no-free-text-param invariants

## Decisions Made

- `asyncio.run()` instead of adding `pytest-asyncio` as a dev dependency — the gateway's only async surface is `execute_tool()` itself, and stdlib `asyncio.run()` inside a plain sync `pytest` test covers that fully without a new dependency.
- Reworded `gemini_client.py`'s docstrings to avoid the literal substring `ClientSession` anywhere in the file (not just imports), since the plan's own automated verify step (`assert 'ClientSession' not in src`) greps the entire file text including comments/docstrings, not just code.

## Deviations from Plan

None - plan executed exactly as written. Both tasks' automated verify commands and acceptance criteria pass as specified.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. This plan's test suite runs entirely against fakes (`FakeMCPManager`) and never calls the live Gemini or MCP APIs, so no `GEMINI_API_KEY` was needed. Note: `backend/.env` (gitignored, referenced in 01-03's summary) does not exist in this worktree — worktrees only check out tracked files, and `.env` was never committed by design. This plan's test suite never imports `config.py`/`get_settings()`, so it was not a blocker; a real `GEMINI_API_KEY` is still required before 01-05 (agent loop) can make live Gemini calls.

## Next Phase Readiness

- `GeminiClient`/`ToolExecutionGateway` public surfaces are stable and ready for `01-05`'s agent loop to wire together: call `gemini_client.generate()`, read `.function_calls`, hand each call to `gateway.execute_tool()`, serialize the returned `ToolResult` into a `function_response` `Part` via `gemini_client.function_response_part()`.
- The sole-caller invariant (`mcp_manager.call(` appears only in `gateway.py`) is grep-verified now and must remain true after `01-05` adds `agent_loop.py` — the agent loop must receive only a read-only `list_all_tools()`/`server_for()` view, never the manager's `call()` capability directly.
- No blockers for `01-05`, other than the real `GEMINI_API_KEY` requirement (unchanged from 01-03's note).

---
*Phase: 01-core-loop-terminal-verified*
*Completed: 2026-07-09*
