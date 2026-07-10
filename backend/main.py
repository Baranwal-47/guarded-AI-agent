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
from gemini_client import GeminiClient
from google.genai import types
from mcp_manager import MCPManager
from models import ApprovalRequest, AuditLog, Conversation, Message, PolicyRule, ToolExecution
from pydantic import BaseModel, model_validator
from sqlalchemy import func, select
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


async def _load_or_create_conversation() -> tuple[list, str]:
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
        return contents, conversation.id


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

        contents, conversation_id = await _load_or_create_conversation()
        print(f"[STARTUP] Resumed conversation {conversation_id}: {len(contents)} prior messages loaded")

        app.state.agent_loop = agent_loop
        app.state.contents = contents
        app.state.conversation_id = conversation_id
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

    rule_type: Literal["block_tool", "require_approval", "input_validation", "token_budget"]
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


@app.post("/chat")
async def chat(body: ChatRequest) -> dict:
    app.state.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=body.message)]))
    await _persist_message(app.state.conversation_id, "user", body.message)

    final_text, app.state.contents = await app.state.agent_loop.run_turn(
        app.state.contents, app.state.conversation_id, token_usage=0
    )

    # ponytail: persist only the completed turn (user text + assistant final
    # text) — a turn finishes inside one synchronous /chat call (D-02), so
    # mid-turn tool-call crash recovery is out of scope this phase.
    await _persist_message(app.state.conversation_id, "model", final_text)

    return {"final_text": final_text}


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
