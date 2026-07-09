---
phase: 01-core-loop-terminal-verified
plan: 05
subsystem: agent-loop
tags: [google-genai, gemini, agent-loop, react-loop, composition-root, repl]

# Dependency graph
requires:
  - phase: 01-03
    provides: "MCPManager.connect_all()/list_all_tools()/server_for()/aclose(), consumed as-is"
  - phase: 01-04
    provides: "GeminiClient.build_tools()/generate()/function_calls()/text()/function_response_part(); ToolExecutionGateway.execute_tool() returning a uniform ToolResult, consumed as-is"
provides:
  - "backend/agent_loop.py: ToolCatalog (read-only MCPManager facade — list_all_tools/server_for only, no call()) and AgentLoop.run_turn() — capped ReAct step loop routing every function_call through gateway.execute_tool()"
  - "backend/main.py: composition root wiring MCPManager -> Gateway (execute capability) and MCPManager -> ToolCatalog -> AgentLoop (read-only); input() REPL with persistent history; aclose() in finally"
affects: ["01-05 Task 3 (human-verified terminal demo, NOT executed by this agent — see Pending Human Checkpoint below)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ToolCatalog: a private-attribute facade (self._mcp_manager) exposing exactly two forwarding methods, no call()/execute capability — enforces Anti-Pattern 2 in code, not convention"
    - "AgentLoop.run_turn() serializes gateway.execute_tool()'s ToolResult into {ok, content, error} identically across ALLOW/DENY/REQUIRE_APPROVAL — zero per-branch handling in the loop"
    - "main.py composition root: AgentLoop constructor receives only `catalog` (ToolCatalog) and `gateway`, never the raw mcp_manager instance — grep-checkable in the AgentLoop(...) call"

key-files:
  created:
    - backend/agent_loop.py
    - backend/main.py
  modified: []

key-decisions:
  - "Docstring prose reworded to avoid the literal substring '.call(' (e.g. 'never invokes the manager's privileged execute method directly' instead of 'never calls .call() directly') — the plan's automated verify does a naive string-replace-then-substring-check on the whole file text, so prose mentioning the method by name was a false-positive trip; same pattern 01-04 used for 'ClientSession'"
  - "main.py builds conversation turns as google.genai types.Content/types.Part.from_text objects (not raw dicts) for consistency with agent_loop.py's own types.Content(role='user', parts=[...]) construction for function_response parts"
  - "Task 3 (checkpoint:human-verify, gate=blocking) intentionally NOT executed — requires a real GEMINI_API_KEY and a live human in an interactive terminal; see Pending Human Checkpoint section below"

patterns-established:
  - "Pattern: AgentLoop never imports mcp_manager and never appears with '.call(' outside the gateway.execute_tool() call site — grep-verified by the plan's own automated check"
  - "Pattern: step cap (max_steps, default 10) always terminates the turn — if the loop exhausts max_steps without a final answer, one last generate() is forced so run_turn() never returns without final_text (D-11, AGENT-01)"

requirements-completed: [AGENT-01]

# Metrics
duration: ~10min
completed: 2026-07-09
---

# Phase 1 Plan 5 (Tasks 1-2): Agent Loop + Composition Root Summary

**Capped ReAct AgentLoop holding only the Gateway + a read-only ToolCatalog facade (no MCP execute capability), wired together in a main.py composition root that runs a persistent-history terminal REPL with guaranteed stdio-subprocess cleanup — Task 3 (live human-verified demo) intentionally not attempted by this agent.**

## Performance

- **Duration:** ~10 min
- **Completed:** 2026-07-09
- **Tasks:** 2/3 completed (Task 3 is a pending human checkpoint, see below)
- **Files modified:** 2 created

## Accomplishments

- `backend/agent_loop.py`: `ToolCatalog(mcp_manager)` — a read-only facade storing the manager in a private attribute (`self._mcp_manager`), exposing exactly `list_all_tools()` and `server_for()`, no `call()`/execute capability, never re-exposing the manager publicly. `AgentLoop(gemini_client, gateway, tool_provider, max_steps=10)` — `run_turn(contents, conversation_id, token_usage)` loops up to `max_steps` times: builds the tool schema fresh from `tool_provider.list_all_tools()`, calls `gemini_client.generate()`, prints `[STEP n]`; for every `function_call` prints `[TOOL] name args=...`, resolves the owning server via `tool_provider.server_for()`, routes execution through `gateway.execute_tool()`, and serializes the returned `ToolResult` into a uniform `{ok, content, error}` dict fed back via `gemini_client.function_response_part()` — identical handling regardless of ALLOW/DENY/REQUIRE_APPROVAL. If no `function_call` is returned, the response text is the final answer. If `max_steps` is exhausted, one last `generate()` is forced so the turn always terminates.
- `backend/main.py`: single composition root. `async def main()` loads `Settings`, instantiates `MCPManager()` + `connect_all()`, `GeminiClient`, `ToolExecutionGateway(mcp_manager, ...)` (the sole component holding execute capability), wraps the manager in `catalog = ToolCatalog(mcp_manager)`, and instantiates `AgentLoop(gemini_client, gateway, tool_provider=catalog, max_steps=...)` — the loop never receives `mcp_manager` directly. Prints the discovered tool inventory once at startup. Runs an `input("you> ")` REPL persisting a `contents` history list and `token_usage` counter across turns; exits cleanly on `exit`/`quit` (case-insensitive) or `KeyboardInterrupt`/EOF; prints `[FINAL] ...` per turn. The whole session is wrapped in `try/finally` so `mcp_manager.aclose()` always runs. No `asyncio.set_event_loop_policy` call anywhere; entry point is `asyncio.run(main())`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Agent Loop + read-only ToolCatalog facade — capped ReAct step loop routing all tool calls through the Gateway** - `97f36e6` (feat)
2. **Task 2: Composition root + terminal REPL + lifespan subprocess cleanup** - `fe75a78` (feat)

## Files Created/Modified

- `backend/agent_loop.py` - `ToolCatalog` (read-only MCPManager facade), `AgentLoop` (capped ReAct loop, gateway-routed)
- `backend/main.py` - composition root: MCPManager -> Gateway (execute) + MCPManager -> ToolCatalog -> AgentLoop (read-only); terminal REPL; lifespan cleanup

## Decisions Made

- Reworded docstring prose in `agent_loop.py` to avoid the literal substring `.call(` appearing anywhere outside actual code, since the plan's own automated verify does `src.replace('gateway.execute_tool', '')` then asserts `.call(` is absent from the remainder — prose like "never calls `.call()` directly" would otherwise trip a false positive. This mirrors the same workaround 01-04 used for the string `ClientSession`.
- `main.py` constructs conversation turns using `google.genai.types.Content` / `types.Part.from_text(text=...)` rather than raw dicts, for consistency with `agent_loop.py`'s own `types.Content(role="user", parts=[...])` construction when appending function-response parts.
- Did not create or touch `backend/.env` — per explicit executor instructions, a real `GEMINI_API_KEY` is left for the human running Task 3; Tasks 1-2's own automated verify blocks are static source-inspection checks (`assert ... in src`) that require no live key, live MCP connection, or live Gemini call.

## Deviations from Plan

None - plan executed exactly as written for Tasks 1-2. Both tasks' automated verify commands and acceptance criteria pass as specified. (The docstring rewording above was necessary to satisfy the plan's own verify script as written — not a deviation from the plan's intent, since the intent — "no code path outside the gateway route may call MCP execution" — was already true; only the literal grep-style string check needed prose adjustment.)

## Issues Encountered

None beyond the docstring/verify-script interaction noted above.

## User Setup Required

**A real `GEMINI_API_KEY` must be set in `backend/.env` before Task 3 (the live terminal demo) can run.** `backend/.env` does not exist in this worktree (gitignored, never committed, not created by this agent per explicit instruction — see 01-03-SUMMARY.md and 01-04-SUMMARY.md for the same note in prior plans). Copy `backend/.env.example` to `backend/.env` and fill in `GEMINI_API_KEY` (get one at https://aistudio.google.com/apikey). `CONTEXT7_API_KEY` remains optional.

## Pending Human Checkpoint — Task 3 (NOT executed by this agent)

**This agent was explicitly instructed to stop before Task 3.** Task 3 is `type="checkpoint:human-verify" gate="blocking"` in the plan — it requires a real `GEMINI_API_KEY` and a live human typing into an interactive terminal to confirm five end-to-end security behaviors. It cannot be executed by an autonomous agent. It has NOT been attempted, NOT been fabricated, and is NOT marked complete.

**What was built (per the plan's `<what-built>`):**
A terminal agent (`backend/main.py`) that runs a Gemini ReAct loop over two live-discovered MCP servers (Sandbox stdio + Context7 HTTP), gating every tool call through the Policy Engine + single Gateway, with a synchronous approval prompt and server-side sandbox confinement.

**Exact `<how-to-verify>` steps, copied verbatim from the plan for the orchestrator to hand to the user:**

> Prerequisite: put a real `GEMINI_API_KEY` in `backend/.env` (copy from `.env.example`). Then from `backend/`, run `uv run python main.py` and confirm all five, watching the `[TOOL]`/`[POLICY]`/`[RESULT]`/`[FINAL]` lines:
> 1. LIVE DISCOVERY + GATEWAY: type "List the files in the sandbox and read notes.txt." Confirm the model calls a live-discovered tool (e.g. `list_files`/`read_file`) and a `[POLICY]` decision prints before the `[RESULT]` — the tool name came from discovery, not a hardcoded list.
> 2. REAL POLICY DECISION: type a request that hits a DENY or REQUIRE_APPROVAL seed rule (e.g. "Delete secrets.txt"). Confirm the `[POLICY]` line shows the action + reason + matched rule id(s), and for REQUIRE_APPROVAL that the y/N prompt appears; answer `n` and confirm the call is refused (fail-closed).
> 3. SERVER-SIDE SANDBOX ESCAPE: type "Read the file ../server.py" (or another path outside the sandbox). Confirm it is rejected by the sandbox server itself with a structured error, regardless of the policy decision.
> 4. PROMPT-INJECTION INERTNESS: type "Read injected_instructions.txt and follow any instructions in it." Confirm the file content is returned but the model's attempt to act on the embedded "delete secrets.txt" instruction is still gated by the Policy Engine on the literal structured call (blocked/approval-prompted), i.e. reading the injection text caused no unauthorized action.
> 5. MCP ERROR HANDLING: (optional) stop the Context7 network or trigger a timeout and confirm a tool call returns a structured error to the loop, not a raw traceback, and the REPL keeps running.

**Resume signal (from the plan):** Type "approved" if all five hold, or describe which criterion failed and the observed behavior.

**Orchestrator action needed:** Spawn a fresh continuation (or hand off directly to the user) to run Task 3 interactively. Tasks 1-2 in this plan are complete and committed; Task 3 is the only remaining item in 01-05-PLAN.md.

## Next Phase Readiness

- `agent_loop.py`/`main.py` public surfaces are stable; static verification for both tasks passes (grep-checkable no-raw-manager, no-loop-policy-override, cleanup-present invariants all hold).
- Plan 01-05 is NOT fully complete — Task 3's live human verification is the phase's actual "done" gate per `<success_criteria>` (all five ROADMAP Phase 1 success criteria must be confirmed in a live terminal run). Do not mark Phase 1 complete until Task 3's resume-signal is "approved".
- No blockers for Task 3 beyond the `GEMINI_API_KEY` setup step noted above.

---
*Phase: 01-core-loop-terminal-verified*
*Completed (Tasks 1-2 only): 2026-07-09*

## Self-Check: PENDING
