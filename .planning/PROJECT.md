# Guarded AI Agent

## What This Is

A full-stack "guarded" AI agent: a FastAPI backend runs a Gemini-powered tool-use loop over MCP servers (the remote Context7 server plus a custom Sandbox File Manager server), with every tool call routed through a policy engine and an optional human-approval step. A React dashboard lets you write guardrail rules, approve/reject pending tool calls, and watch audit logs live. Originally scoped from a take-home assignment (ArmorIQ SWE intern assignment); now being built as a personal project — the ArmorIQ branding/submission process is irrelevant, but the core technical bar (live MCP tool discovery, a real policy boundary, no-restart rule updates) still stands.

## Core Value

Every MCP tool execution passes through exactly one Tool Execution Gateway that checks the Policy Engine first. The LLM is untrusted; the policy layer — not the model — is the actual security boundary, and dashboard rule changes take effect on the running agent without a restart.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Agent runs a real tool-use loop (LLM → tool call → MCP execute → result fed back → LLM continues) against Gemini 2.5 Flash
- [ ] MCP Manager connects to ≥2 MCP servers (Context7 remote + custom Sandbox File Manager) and discovers tools live via `tools/list` — no hardcoded tool lists anywhere
- [ ] Custom Sandbox File Manager MCP server exposes 5 tools (`list_files`, `read_file`, `write_file`, `move_file`, `delete_file`), confined to a real sandbox directory, enforced server-side (defense-in-depth) independent of the policy engine
- [ ] Policy Engine is a separate, self-contained module (not inline in the agent loop) that evaluates every tool call and returns ALLOW / DENY / REQUIRE_APPROVAL with reason + matched rules, precedence DENY > REQUIRE_APPROVAL > ALLOW
- [ ] Dashboard: Policies page to create/toggle rules (block tool, require approval, input validation e.g. path prefix, token budget per conversation)
- [ ] Dashboard: Approvals page — pending tool calls shown live, admin can Approve/Reject; 5-minute timeout auto-denies (fail closed)
- [ ] Dashboard: Agent chat page — single ongoing conversation, shows tool calls and policy decisions inline
- [ ] Dashboard: Audit Logs page — tool requested, policy decision, final decision, execution result, timestamped
- [ ] Policy/rule changes take effect on the next tool call with no backend restart (shared DB, no in-process cache invalidation needed for v1)
- [ ] Real-time push of tool/approval/policy events to the dashboard via WebSocket
- [ ] MCP call failure handling: timeout, single retry only for idempotent ops, structured error surfaced to the LLM otherwise
- [ ] Point of view (implemented, not just discussed) on prompt injection: policy engine independently checks structured tool calls regardless of what the model was told; optional `PROMPT_INJECTION_SUSPECTED` logging as a bonus, not the primary defense

### Out of Scope

- Authentication/login — single-user localhost tool, no admin accounts
- Multiple saved conversations / thread switcher UI — one ongoing conversation is enough for v1
- Deployment (Docker, Vercel, Railway/Render, Postgres) — stretch goal, not committed scope for this milestone; local dev (SQLite) is the target
- Broad automated test suite — only a handful of pytest checks on the Policy Engine; no test coverage elsewhere for v1
- Any agent framework (LangChain, LangGraph, CrewAI, DynaMIQ) — custom loop only, for full control and a readable LLM→Policy→MCP boundary
- ArmorIQ submission logistics (email submission, deployed link, 5-min recording) — not applicable, personal project

## Context

- Sourced from a take-home brief ("Build a Guarded AI Agent with MCP Support") with a hard technical bar: tool discovery must be live (never hardcoded), ≥2 MCP servers (1 remote existing + 1 self-built), policy engine must be a separate self-contained module, dashboard changes must propagate without restart, code must be cleanly split (agent / policy / MCP transport / gateway are separate modules, not one giant file).
- Brief's edge cases to have a point of view on (design for these, don't necessarily gold-plate): MCP server crash mid-call; agent attempting to bypass a guardrail via prompt injection; two guardrail rules conflicting; a tool needing approval while the approver is offline (timeout → fail closed).
- This machine's MCP development environment (Python 3.12/uv, FastMCP, Windows/Claude Desktop specifics) is already documented in the `mcp-development` Claude Code skill — reuse those conventions when building the custom Sandbox File Manager server.
- User explicitly wants no "AI slop": simple, readable, functional code over clever abstractions. No framework for a single implementation, no speculative extensibility.

## Constraints

- **Stack**: React + Vite + TypeScript + Tailwind (frontend), FastAPI + Python (backend), Gemini 2.5 Flash (LLM/tool-calling), official Python MCP SDK, SQLite + SQLAlchemy (persistence), FastAPI WebSockets (real-time) — chosen to minimize framework-learning overhead and keep the policy boundary easy to inspect
- **No agent framework**: custom ReAct-style loop only — full control over the LLM → Policy → MCP boundary, not hidden behind a library
- **Single Tool Execution Gateway**: no code path may call MCP `tools/call` except through the gateway, which always consults the Policy Engine first
- **Local dev first**: this milestone targets `localhost` only; deployment is a stretch goal, not a blocking requirement
- **No auth**: trust the local browser session
- **Monorepo layout**: this repo (`guarded-AI-agent`) holds `backend/`, `frontend/`, and `mcp-servers/sandbox-file-manager/`
- **Timeline**: informally pitched as 3 days in the original brief; realistically expected to take about a day with AI-assisted development — not a hard deadline, just don't over-build

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Custom ReAct-style tool loop, no agent framework | Full control over LLM→Policy→MCP boundary; whole orchestration readable in one file | — Pending |
| Gemini 2.5 Flash for tool-calling | Fast, cheap, reliable native function-calling; no need for heavier reasoning | — Pending |
| Policy precedence: DENY > REQUIRE_APPROVAL > ALLOW | Deterministic, documented resolution when rules conflict | — Pending |
| Custom MCP server = Sandbox File Manager (stdio) | Clearest possible demo of block/approve/path-validation guardrails; MCP server also self-enforces the sandbox path (defense-in-depth) | — Pending |
| No auth, single conversation, local-dev first, deployment/tests kept minimal | Personal project — control scope aggressively so the core policy/MCP loop actually ships | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-07-09 after initialization*
