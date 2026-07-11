"""Composition root + FastAPI app.

Wires MCPManager -> Gateway (privileged execute capability) and
MCPManager -> ToolCatalog -> AgentLoop (read-only) — the AgentLoop receives
only the Gateway plus the read-only `ToolCatalog` facade, never the raw
`MCPManager` (ARCHITECTURE.md Pattern 1, Anti-Pattern 2). `POST /chat` drives
one full agent turn synchronously and returns the final answer in the
response body (D-01, D-02). On startup, prior conversation history is
eager-loaded from SQLite and used to seed `contents`, with a resume line
logged (D-08, D-09). Never overrides the asyncio event loop policy — let
uvicorn/asyncio use the platform default so stdio subprocess spawning keeps
working on Windows (Pitfall 1).
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import yaml
from agent_loop import AgentLoop, ToolCatalog
from approval_manager import ApprovalManager
from config import get_settings
from db import async_session, init_models
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from gateway import ToolExecutionGateway, reconcile_pending_approvals, try_decide
from gemini_client import GeminiClient, LLMUnavailableError
from google.genai import types
from mcp_manager import MCPManager
from models import ApprovalRequest, AuditLog, Conversation, Message, PolicyRule, ToolExecution
from pydantic import BaseModel, model_validator
from sqlalchemy import delete, func, select
from ws_manager import WebSocketManager


async def _seed_policy_rules_if_empty(rules_path: str) -> None:
    """One-time migration: populate the policy_rules table from the YAML
    seed file the first time the table is empty. Rules live in the DB from
    then on (POLICY-04) — this never runs again once rows exist."""
    async with async_session() as session:
        count = (await session.execute(select(func.count()).select_from(PolicyRule))).scalar_one()
        if count:
            return

        data = yaml.safe_load(Path(rules_path).read_text()) or {}
        rules = data.get("rules", [])
        for r in rules:
            session.add(
                PolicyRule(
                    id=r["id"],
                    rule_type=r["rule_type"],
                    tool_name=r["tool_name"],
                    condition=r.get("condition") or {},
                    action=r["action"],
                    enabled=r.get("enabled", True),
                )
            )
        await session.commit()
        print(f"[STARTUP] seeded {len(rules)} policy rules")


async def _load_or_create_conversation() -> tuple[list, str, int]:
    """Eager-load the single ongoing conversation's full history into a
    `contents` list, entirely inside one session scope (Pitfall 5 — no lazy
    relationship traversal, no access after the session closes).

    # ponytail: history is loaded in full, unbounded — accepted per T-02-02
    # (single-user localhost, one ongoing conversation, growth bounded by
    # manual use). Add pagination/truncation if this ever stops holding.
    """
    async with async_session() as session:
        conversation = (await session.execute(select(Conversation))).scalars().first()
        if conversation is None:
            conversation = Conversation()
            session.add(conversation)
            await session.commit()

        rows = (
            await session.execute(
                select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)
            )
        ).scalars().all()
        contents = [types.Content(role=m.role, parts=[types.Part.from_text(text=m.content)]) for m in rows]
        return contents, conversation.id, conversation.token_usage


async def _reconcile_startup_approvals() -> None:
    """APPROVAL-03: any approval_requests row still PENDING from a prior
    process has no surviving Future/timer — deny every orphan fail-closed
    before the app accepts /chat or /approvals traffic (RESEARCH Pattern 4)."""
    async with async_session() as session:
        reconciled = await reconcile_pending_approvals(session)
        if reconciled:
            print(f"[STARTUP] reconciled {reconciled} orphaned PENDING approval(s) -> DENIED (fail-closed)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_models()
    await _seed_policy_rules_if_empty(settings.policy_rules_path)
    await _reconcile_startup_approvals()

    mcp_manager = MCPManager()
    await mcp_manager.connect_all()

    try:
        gemini_client = GeminiClient(settings.gemini_api_key, settings.gemini_model)
        approval_manager = ApprovalManager()
        ws_manager = WebSocketManager()
        # The Gateway is the ONE component that legitimately holds the manager's
        # privileged execute capability.
        gateway = ToolExecutionGateway(
            mcp_manager,
            async_session,
            approval_manager,
            timeout_seconds=settings.approval_timeout_seconds,
            broadcast=ws_manager.broadcast,
        )
        # The Agent Loop receives only this read-only facade — never mcp_manager.
        catalog = ToolCatalog(mcp_manager)
        agent_loop = AgentLoop(gemini_client, gateway, tool_provider=catalog, max_steps=settings.max_agent_steps)

        contents, conversation_id, token_usage = await _load_or_create_conversation()
        print(f"[STARTUP] Resumed conversation {conversation_id}: {len(contents)} prior messages loaded")

        app.state.agent_loop = agent_loop
        app.state.contents = contents
        app.state.conversation_id = conversation_id
        app.state.token_usage = token_usage
        # Shared with POST /approvals/{id} — same instance the gateway blocks on.
        app.state.approval_manager = approval_manager
        # Shared with the /ws route — same instance the gateway broadcasts through.
        app.state.ws_manager = ws_manager
        # Shared with 03-02's GET /tools route.
        app.state.catalog = catalog

        yield
    finally:
        await mcp_manager.aclose()


app = FastAPI(lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]


class PolicyRuleCreate(BaseModel):
    """Validates the condition shape per rule_type at the API boundary,
    mirroring policy_engine._matches()'s expectations (T-03-04) — a
    malformed rule is rejected with 422 here instead of only failing,
    fail-closed, the first time evaluate() hits it."""

    rule_type: Literal["allow_tool", "block_tool", "require_approval", "input_validation", "token_budget"]
    tool_name: str
    condition: dict = {}
    action: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_condition_shape(self) -> "PolicyRuleCreate":
        if self.rule_type == "input_validation":
            if not isinstance(self.condition.get("prefix"), str):
                raise ValueError("input_validation rule requires a string 'prefix' condition field")
        elif self.rule_type == "token_budget":
            if not isinstance(self.condition.get("max_tokens"), int):
                raise ValueError("token_budget rule requires an int 'max_tokens' condition field")
        elif self.condition:
            raise ValueError(f"{self.rule_type} rule must have an empty condition")
        return self


class PolicyRuleToggle(BaseModel):
    enabled: bool


def _rule_to_dict(rule: PolicyRule) -> dict:
    return {
        "id": rule.id,
        "policy_id": rule.policy_id,
        "rule_type": rule.rule_type,
        "tool_name": rule.tool_name,
        "condition": rule.condition,
        "action": rule.action,
        "enabled": rule.enabled,
    }


async def _persist_message(conversation_id: str, role: str, content: str) -> None:
    async with async_session() as session:
        session.add(Message(conversation_id=conversation_id, role=role, content=content))
        await session.commit()


async def _persist_token_usage(conversation_id: str, token_usage: int) -> None:
    async with async_session() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.token_usage = token_usage
        await session.commit()


async def _persist_llm_failure(conversation_id: str, detail: str) -> None:
    async with async_session() as session:
        session.add(AuditLog(event="llm_unavailable", detail={"conversation_id": conversation_id, "error": detail}))
        await session.commit()


@app.post("/chat")
async def chat(body: ChatRequest) -> dict:
    app.state.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=body.message)]))
    await _persist_message(app.state.conversation_id, "user", body.message)

    try:
        final_text, app.state.contents, app.state.token_usage = await app.state.agent_loop.run_turn(
            app.state.contents, app.state.conversation_id, app.state.token_usage
        )
    except LLMUnavailableError as exc:
        # The user's turn stays persisted (it's a real message they sent); no
        # assistant reply is persisted for a turn the LLM never completed.
        # Next turn's contents will carry the dangling user message forward —
        # Gemini handles a stray unanswered user turn fine.
        await _persist_llm_failure(app.state.conversation_id, str(exc))
        raise HTTPException(status_code=503, detail=f"LLM temporarily unavailable: {exc}") from exc

    # ponytail: persist only the completed turn (user text + assistant final
    # text) — a turn finishes inside one synchronous /chat call (D-02), so
    # mid-turn tool-call crash recovery is out of scope this phase.
    await _persist_message(app.state.conversation_id, "model", final_text)
    await _persist_token_usage(app.state.conversation_id, app.state.token_usage)

    return {"final_text": final_text}


@app.delete("/chat")
async def clear_chat() -> dict:
    """Wipes the single ongoing conversation's persisted messages and tool-call
    history, and resets in-memory state (contents, token_usage) so a restart
    resumes empty instead of replaying stale rows (T-02-02's single-
    conversation design has no separate "new chat" concept, so clearing means
    resetting this one). AuditLog is a separate, permanent security trail and
    is deliberately left untouched."""
    async with async_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == app.state.conversation_id))
        await session.execute(delete(ToolExecution).where(ToolExecution.conversation_id == app.state.conversation_id))
        conversation = await session.get(Conversation, app.state.conversation_id)
        conversation.token_usage = 0
        await session.commit()
    app.state.contents = []
    app.state.token_usage = 0
    return {"ok": True}


@app.post("/approvals/{request_id}")
async def resolve_approval(request_id: str, body: ApprovalDecision) -> dict:
    """Resolve a pending REQUIRE_APPROVAL tool call (APPROVAL-02). The
    conditional UPDATE...WHERE status='PENDING' in try_decide() is the sole
    arbiter — ok=False means the request was already decided (by the
    auto-deny timer or an earlier POST), a no-op, not an error."""
    new_status = "APPROVED" if body.decision == "approve" else "DENIED"
    async with async_session() as session:
        won = await try_decide(session, request_id, new_status, "human")
    if won:
        app.state.approval_manager.wake(request_id, body.decision)
    return {"ok": won}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """Dashboard's live event feed — see backend/ws_manager.py for the
    fan-out invariant and gateway.py for the 8 lifecycle event types."""
    await app.state.ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        app.state.ws_manager.disconnect(websocket)


@app.get("/tools")
async def list_tools() -> list[dict]:
    """D-05: thin read-only wrapper over the existing ToolCatalog — zero new
    discovery logic, no hardcoded tool names."""
    return app.state.catalog.list_all_tools()


@app.post("/policies/rules")
async def create_policy_rule(body: PolicyRuleCreate) -> dict:
    async with async_session() as session:
        rule = PolicyRule(
            rule_type=body.rule_type,
            tool_name=body.tool_name,
            condition=body.condition,
            action=body.action,
            enabled=body.enabled,
        )
        session.add(rule)
        await session.commit()
        return {"id": rule.id}


@app.get("/policies/rules")
async def list_policy_rules() -> list[dict]:
    # ponytail: PolicyRule has no created_at column, so there's no
    # chronological ordering to sort by here; D-08 groups by tool_name
    # client-side anyway, so insertion order is not load-bearing.
    async with async_session() as session:
        rows = (await session.execute(select(PolicyRule))).scalars().all()
        return [_rule_to_dict(r) for r in rows]


@app.patch("/policies/rules/{rule_id}")
async def toggle_policy_rule(rule_id: str, body: PolicyRuleToggle) -> dict:
    """D-07: toggle only — no route edits tool_name/condition/action."""
    async with async_session() as session:
        rule = await session.get(PolicyRule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        rule.enabled = body.enabled
        await session.commit()
    return {"ok": True}


@app.delete("/policies/rules/{rule_id}")
async def delete_policy_rule(rule_id: str) -> dict:
    async with async_session() as session:
        rule = await session.get(PolicyRule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        await session.delete(rule)
        await session.commit()
    return {"ok": True}


def _approval_to_dict(r: ApprovalRequest) -> dict:
    return {
        "id": r.id,
        "tool_name": r.tool_name,
        "arguments": r.arguments,
        "reason": r.reason,
        "status": r.status,
        "decided_by": r.decided_by,
        "created_at": r.created_at.isoformat(),
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
    }


async def _fetch_pending_approvals(session) -> list[dict]:
    """Shared by GET /approvals?status=pending and GET /chat/state (Pitfall
    5 — a client that connects after a REQUIRE_APPROVAL was already
    broadcast has no other way to learn about it; WS broadcasts aren't
    replayed to late joiners)."""
    rows = (
        await session.execute(
            select(ApprovalRequest).where(ApprovalRequest.status == "PENDING").order_by(ApprovalRequest.created_at.desc())
        )
    ).scalars().all()
    return [_approval_to_dict(r) for r in rows]


@app.get("/approvals")
async def list_approvals(status: str = "PENDING") -> list[dict]:
    async with async_session() as session:
        if status.upper() == "PENDING":
            return await _fetch_pending_approvals(session)
        rows = (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.status == status.upper())
                .order_by(ApprovalRequest.created_at.desc())
            )
        ).scalars().all()
        return [_approval_to_dict(r) for r in rows]


@app.get("/audit/executions")
async def list_tool_executions(
    tool_name: str | None = None, decision: str | None = None, limit: int = 200
) -> list[dict]:
    # T-03-05: clamp unconditionally — client-supplied limit can only lower,
    # never raise, the server-side cap (DoS mitigation).
    effective_limit = min(limit, 200)
    async with async_session() as session:
        query = select(ToolExecution).order_by(ToolExecution.created_at.desc()).limit(effective_limit)
        if tool_name:
            query = query.where(ToolExecution.tool_name == tool_name)
        if decision:
            query = query.where(ToolExecution.decision_action == decision)
        rows = (await session.execute(query)).scalars().all()
        return [
            {
                "id": r.id,
                "conversation_id": r.conversation_id,
                "tool_name": r.tool_name,
                "arguments": r.arguments,
                "decision_action": r.decision_action,
                "decision_reason": r.decision_reason,
                "matched_rule_ids": r.matched_rule_ids,
                "result_ok": r.result_ok,
                "result_error": r.result_error,
                "flagged_prompt_injection": r.flagged_prompt_injection,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


@app.get("/chat/state")
async def chat_state() -> dict:
    """D-04: the Agent page calls this on mount and on every WS reconnect so
    a dropped connection during a long approval wait never strands the page
    on stale state. Also the only way a (re)mounted Agent page recovers
    already-resolved tool-call blocks — the live WS stream only reaches
    whichever tab is open at the moment a gateway event fires, so a page that
    mounts after the fact (e.g. navigating back from Approvals) has no other
    way to learn a tool call happened."""
    async with async_session() as session:
        pending_approvals = await _fetch_pending_approvals(session)
        recent_rows = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == app.state.conversation_id)
                .order_by(Message.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
        recent_tool_calls = (
            await session.execute(
                select(ToolExecution)
                .where(ToolExecution.conversation_id == app.state.conversation_id)
                .order_by(ToolExecution.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
    return {
        "pending_approvals": pending_approvals,
        "recent_messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in reversed(recent_rows)
        ],
        "recent_tool_calls": [
            {
                "tool_name": r.tool_name,
                "arguments": r.arguments,
                "decision_action": r.decision_action,
                "decision_reason": r.decision_reason,
                "matched_rule_ids": r.matched_rule_ids,
                "result_ok": r.result_ok,
                "result_error": r.result_error,
                "created_at": r.created_at.isoformat(),
            }
            for r in reversed(recent_tool_calls)
        ],
        "token_usage": app.state.token_usage,
    }


@app.get("/audit/logs")
async def list_audit_logs(event: str | None = None, limit: int = 200) -> list[dict]:
    effective_limit = min(limit, 200)
    async with async_session() as session:
        query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(effective_limit)
        if event:
            query = query.where(AuditLog.event == event)
        rows = (await session.execute(query)).scalars().all()
        return [
            {
                "id": r.id,
                "event": r.event,
                "detail": r.detail,
                "flags": r.flags,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
