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

from agent_loop import AgentLoop, ToolCatalog
from config import get_settings
from db import async_session, init_models
from fastapi import FastAPI
from gateway import ToolExecutionGateway
from gemini_client import GeminiClient
from google.genai import types
from mcp_manager import MCPManager
from models import Conversation, Message
from pydantic import BaseModel
from sqlalchemy import select


async def _load_or_create_conversation() -> tuple[list, str]:
    """Eager-load the single ongoing conversation's full history into a
    `contents` list, entirely inside one session scope (Pitfall 5 — no lazy
    relationship traversal, no access after the session closes)."""
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_models()

    mcp_manager = MCPManager()
    await mcp_manager.connect_all()

    try:
        gemini_client = GeminiClient(settings.gemini_api_key, settings.gemini_model)
        # The Gateway is the ONE component that legitimately holds the manager's
        # privileged execute capability.
        gateway = ToolExecutionGateway(mcp_manager, settings.policy_rules_path)
        # The Agent Loop receives only this read-only facade — never mcp_manager.
        catalog = ToolCatalog(mcp_manager)
        agent_loop = AgentLoop(gemini_client, gateway, tool_provider=catalog, max_steps=settings.max_agent_steps)

        contents, conversation_id = await _load_or_create_conversation()
        print(f"[STARTUP] Resumed conversation {conversation_id}: {len(contents)} prior messages loaded")

        app.state.agent_loop = agent_loop
        app.state.contents = contents
        app.state.conversation_id = conversation_id

        yield
    finally:
        await mcp_manager.aclose()


app = FastAPI(lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str


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
