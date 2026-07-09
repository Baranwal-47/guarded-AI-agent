<!-- GSD:project-start source:PROJECT.md -->

## Project

**Guarded AI Agent**

A full-stack "guarded" AI agent: a FastAPI backend runs a Gemini-powered tool-use loop over MCP servers (the remote Context7 server plus a custom Sandbox File Manager server), with every tool call routed through a policy engine and an optional human-approval step. A React dashboard lets you write guardrail rules, approve/reject pending tool calls, and watch audit logs live. Originally scoped from a take-home assignment (ArmorIQ SWE intern assignment); now being built as a personal project — the ArmorIQ branding/submission process is irrelevant, but the core technical bar (live MCP tool discovery, a real policy boundary, no-restart rule updates) still stands.

**Core Value:** Every MCP tool execution passes through exactly one Tool Execution Gateway that checks the Policy Engine first. The LLM is untrusted; the policy layer — not the model — is the actual security boundary, and dashboard rule changes take effect on the running agent without a restart.

### Constraints

- **Stack**: React + Vite + TypeScript + Tailwind (frontend), FastAPI + Python (backend), Gemini 2.5 Flash (LLM/tool-calling), official Python MCP SDK, SQLite + SQLAlchemy (persistence), FastAPI WebSockets (real-time) — chosen to minimize framework-learning overhead and keep the policy boundary easy to inspect
- **No agent framework**: custom ReAct-style loop only — full control over the LLM → Policy → MCP boundary, not hidden behind a library
- **Single Tool Execution Gateway**: no code path may call MCP `tools/call` except through the gateway, which always consults the Policy Engine first
- **Local dev first**: this milestone targets `localhost` only; deployment is a stretch goal, not a blocking requirement
- **No auth**: trust the local browser session
- **Monorepo layout**: this repo (`guarded-AI-agent`) holds `backend/`, `frontend/`, and `mcp-servers/sandbox-file-manager/`
- **Timeline**: informally pitched as 3 days in the original brief; realistically expected to take about a day with AI-assisted development — not a hard deadline, just don't over-build

<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->

## Technology Stack

## Resolving the user's MCP SDK version concern (read this first)

- **`mcp==1.28.1` is the current latest stable release** (published 2026-06-26). `pip install mcp` / `uv add mcp` resolves to this today.
- There **is** a "beta v2" — `2.0.0b1` (June 30) plus `2.0.0a1`–`a3` — exactly what the user half-remembered. But it is a pre-release line: PyPI/uv/pip never select it unless you opt in with an exact pin (`mcp==2.0.0b1`). The v1.28.1 README states in bold: *"v1.x remains recommended for production use... add a `<2` upper bound before stable v2 lands."* v2 stable is targeted for the 2026-07-28 spec release — i.e. still weeks away and not something to build this milestone against.
- **v2 does change the API surface** the user was worried about conflating: `FastMCP` is renamed `MCPServer` in v2, the low-level `Server` becomes constructor-injected/snake_case, and there's a new unified `Client(target, mode='auto')`. **None of this applies to v1.28.1.** Pin `mcp>=1.28,<2` and ignore v2 entirely for this project.
- **Server-authoring (FastMCP) vs client-side (`ClientSession`) are indeed two different API surfaces**, and the skill file's imports for both are verified correct at v1.28.1:

## MCP client transports (both needed for this project)

### stdio (custom Sandbox File Manager — local subprocess)

### Streamable HTTP (remote Context7 server)

## Context7's remote MCP server

- **Endpoint:** `https://mcp.context7.com/mcp`
- **Transport:** Streamable HTTP (the `/mcp` path is Streamable HTTP; there is no separate `/sse` legacy endpoint documented for Context7 — use `streamable_http_client`, not `sse_client`).
- **Auth:** API key is **optional** for basic use. Pass it via the `CONTEXT7_API_KEY` header when present. A free key (higher rate limits) is available at `context7.com/dashboard`; without one, Context7 still answers but at lower rate limits — fine for a local single-user dev project.
- **Tools exposed (exactly two):**

## Gemini function-calling — `google-genai`

### Declaring MCP tool schemas to Gemini

### JSON Schema feature gaps (MCP schema → Gemini)

- Confirmed supported (current, per multiple corroborating sources including Google's own recent structured-output announcement): `type`, `properties`, `required`, `items`, `enum`, `format`, `description`, `nullable`, `anyOf`, `$ref`/`$defs` (recently added).
- **Not supported:** `oneOf` is not a distinct case — Gemini treats `oneOf` the same as `anyOf` (i.e., don't rely on `oneOf`'s "exactly one" exclusivity semantics; it's silently loosened to "any one"). Some fields like `default` and certain complex `additionalProperties` shapes have had gaps historically.
- **Practical guidance for this project:** the Sandbox File Manager's tool schemas (five simple file-op tools with string/enum params) will not hit any of these edge cases — plain `type`/`properties`/`required`/`enum` schemas pass through `parameters_json_schema` untouched. Only worry about sanitizing MCP schemas if Context7's tool schemas (which you don't control) turn out to use `oneOf` or unusual keywords; if `generate_content` ever 400s on a tool schema, the fix is a small schema-sanitizing pass (strip `default`, flatten `oneOf`→`anyOf`) before building `FunctionDeclaration`, not a redesign.
- Confidence: MEDIUM — Google's schema-support surface has been actively expanding through 2026 (structured-outputs announcement added `anyOf`/`$ref` support); treat this as a moving target and verify against the live API if a specific tool schema is rejected, rather than assuming the gap list above is exhaustive/permanent.

## FastAPI WebSockets + human-approval blocking pattern

## SQLAlchemy + SQLite — async, not sync

- **Versions:** `sqlalchemy==2.0.51`, `aiosqlite==0.22.1`.
- **Why async here specifically (not just a generic perf argument):** this project's core novel piece is the WebSocket-push + `asyncio.Future`-blocking approval flow, which lives on the FastAPI event loop. If tool-execution/audit-log writes use **sync** SQLAlchemy inside an `async def` route, each DB call blocks the entire event loop for its duration — which also **stalls the WebSocket broadcast loop and every other in-flight approval wait** on the same process, since asyncio is single-threaded per loop. That's a real foot-gun for this exact architecture, not a theoretical one: a slow DB write during a pending approval could delay the dashboard's live event feed. Async SQLAlchemy end-to-end avoids this by construction — DB I/O yields the loop instead of blocking it.
- **Foot-gun to avoid:** don't mix sync `Session` + `async def` routes "because SQLite is fast anyway" — SQLite writes serialize under a global lock, and per the sync-vs-async benchmarks, async-FastAPI-with-sync-DB is *worse* than pure sync (the event loop blocks and there's no thread-pool parallelism benefit for a single SQLite file). Pick one path fully: async routes + `AsyncSession`, engine URL `sqlite+aiosqlite:///./guarded_agent.db`.
- **SQLite-specific config to set once, not discover later:** enable `PRAGMA foreign_keys=ON` (SQLAlchemy doesn't do this by default for SQLite) via an event listener on `"connect"`, and set `connect_args={"check_same_thread": False}` is not needed with `aiosqlite` (it manages its own thread internally) — this is one of the more common sync-SQLAlchemy+SQLite gotchas that async+aiosqlite sidesteps entirely.
- **Model shape** (conversations, messages, policies, policy_rules, approval_requests, tool_executions, audit_logs) is a straightforward relational schema — plain SQLAlchemy 2.0 declarative models (`Mapped[...]`/`mapped_column`) with FK relationships, no need for a heavier ORM feature (no need for `asyncio`-unfriendly lazy-loading patterns; use `selectinload`/explicit eager loading where relationships are read across the async boundary to avoid the classic "implicit IO in `__repr__`/lazy attribute access after session close" async SQLAlchemy trap).
- **Alembic** is optional for this milestone — local dev, single SQLite file, no deployment; `Base.metadata.create_all()` at startup is enough. Don't add migration tooling for a project explicitly scoped to skip deployment.

## Installation

# Backend

# Custom MCP server (mcp-servers/sandbox-file-manager/)

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `mcp==2.0.0b1` / any `2.0.0aN` | Pre-release, API still shifting (`FastMCP`→`MCPServer` rename, new `Client`), not what any current tutorial/tooling targets, unnecessary churn for a local-dev milestone | `mcp>=1.28,<2` (current stable) |
| `mcp.client.sse.sse_client` for Context7 | Context7's endpoint (`/mcp`) is Streamable HTTP; SSE is the SDK's own documented "legacy" transport | `mcp.client.streamable_http.streamable_http_client` |
| Passing a live `ClientSession` directly as a Gemini tool (`tools=[session]`) | Google's own automatic-function-calling loop would call MCP tools directly, completely bypassing the Policy Engine/gateway — breaks the project's core security invariant | Manual `FunctionDeclaration(parameters_json_schema=...)` + `automatic_function_calling=AutomaticFunctionCallingConfig(disable=True)` + hand-rolled loop through the gateway |
| Sync SQLAlchemy `Session` in `async def` FastAPI routes | Blocks the single event loop that also runs WebSocket broadcast and approval-future waits; measurably worse than pure-sync FastAPI in benchmarks, and directly conflicts with this project's approval-timeout mechanism | Async SQLAlchemy (`create_async_engine` + `AsyncSession` + `aiosqlite`) |
| Redis pub/sub or a task queue (Celery/RQ) for the approval/WS flow | Solves a multi-process/multi-instance scaling problem this project explicitly doesn't have (single-user localhost, no deployment this milestone) | In-process `dict[str, asyncio.Future]` + in-process WS connection set |
| Any agent framework (LangChain/LangGraph/CrewAI) | Explicitly out of scope per project constraints — hides the LLM→Policy→MCP boundary the project exists to make visible | Custom loop calling `google-genai` + MCP `ClientSession` + policy module directly |
| Alembic / migration tooling | Local dev only, single SQLite file, no deployment this milestone — pure overhead | `Base.metadata.create_all()` at app startup |

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| `mcp==1.28.1` | Python 3.12 | Matches skill file's documented environment exactly |
| `google-genai==2.10.0` | Python ≥3.10 | Fine alongside `mcp`'s requirements |
| `sqlalchemy==2.0.51` | `aiosqlite==0.22.1` | Use `sqlite+aiosqlite:///` URL scheme, not plain `sqlite:///`, to get the async driver |
| `fastapi==0.139.0` | `uvicorn[standard]==0.51.0`, `websockets==16.0` | `uvicorn[standard]` pulls in `websockets` automatically; no separate pin needed unless you want to control the exact version |
| `mcp>=1.28,<2` | Context7 remote server | Context7 requires only Streamable HTTP support, present since well before 1.28 — no special version coupling |

## Sources

- `https://pypi.org/pypi/mcp/json` — HIGH confidence, live version metadata, fetched directly (not summarized)
- `https://api.github.com/repos/modelcontextprotocol/python-sdk/releases` — HIGH confidence, raw GitHub Releases API, fetched directly
- `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v1.28.1/README.md` and `.../docs/client.md` — HIGH confidence, official docs pinned at the exact stable tag in use
- `https://github.com/modelcontextprotocol/python-sdk/releases/tags/v1.28.0` and `.../v2.0.0b1` (release bodies) — HIGH confidence, official release notes
- `https://raw.githubusercontent.com/upstash/context7/master/README.md` — HIGH confidence, official Context7 repo, current
- `https://pypi.org/pypi/google-genai/json` — HIGH confidence, live version metadata
- `https://raw.githubusercontent.com/googleapis/python-genai/main/README.md` — HIGH confidence, official SDK README, current (`main` branch)
- `https://ai.google.dev/gemini-api/docs/deprecations` — HIGH confidence for gemini-2.5-flash shutdown date, MEDIUM for exact JSON-schema-gap enumeration (fetched via summarization, cross-checked against Firebase AI Logic docs and Google's structured-output blog announcement — sources broadly agree but none gives one exhaustive current list)
- `https://raw.githubusercontent.com/tiangolo/fastapi/master/docs/en/docs/advanced/websockets.md` — HIGH confidence, official FastAPI docs
- WebSearch (SQLAlchemy async/sync FastAPI benchmarks, general asyncio.Future approval patterns) — MEDIUM confidence on specific benchmark numbers cited, but the underlying event-loop-blocking mechanism is HIGH confidence (stdlib asyncio semantics, not opinion)

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
