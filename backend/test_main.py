"""Tests for main.py: FastAPI app, POST /chat persistence, restart resume.

Uses `TestClient(app)` as a context manager so the lifespan actually runs.
`MCPManager`/`GeminiClient` are monkeypatched to no-op fakes so lifespan never
touches a real MCP subprocess or the real Gemini API; the agent turn itself is
driven by a fake `run_turn` swapped onto `app.state.agent_loop` after startup
- same "fake the collaborator, assert the branch" style as test_gateway.py's
FakeMCPManager, just one level up the stack.
"""

import asyncio
import re

import pytest
from fastapi.testclient import TestClient
from google.genai import types
from sqlalchemy import select


class FakeMCPManager:
    """No-op stand-in — lifespan calls connect_all()/aclose(), nothing else
    in these tests ever reaches a real MCP transport."""

    async def connect_all(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

    def list_all_tools(self) -> list[dict]:
        return []

    def server_for(self, tool_name: str) -> str:
        return "fake"


class FakeGeminiClient:
    """No-op stand-in — never invoked because app.state.agent_loop is
    replaced with FakeAgentLoop before any /chat call in these tests."""

    def __init__(self, *args, **kwargs) -> None:
        pass


class FakeAgentLoop:
    """Appends one model Content and returns a fixed final answer, exactly
    like the real AgentLoop.run_turn contract, without touching Gemini/MCP."""

    def __init__(self, final_text: str = "fake final answer") -> None:
        self.final_text = final_text

    async def run_turn(self, contents: list, conversation_id: str, token_usage: int) -> tuple[str, list, int]:
        contents.append(types.Content(role="model", parts=[types.Part.from_text(text=self.final_text)]))
        return self.final_text, contents, token_usage


@pytest.fixture(scope="module")
def main_module(tmp_path_factory):
    """Import main once per module, pointed at a throwaway SQLite file, with
    MCPManager/GeminiClient monkeypatched to fakes. `db.py` binds its engine
    to `get_settings().database_url` at import time, so env vars + cache
    clear MUST happen before the first `import main` (which imports db.py)."""
    db_path = tmp_path_factory.mktemp("data") / "test.db"

    mp = pytest.MonkeyPatch()
    mp.setenv("GEMINI_API_KEY", "test-key")
    mp.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    import config

    config.get_settings.cache_clear()

    import main

    mp.setattr(main, "MCPManager", FakeMCPManager)
    mp.setattr(main, "GeminiClient", FakeGeminiClient)

    yield main

    mp.undo()
    config.get_settings.cache_clear()


def _query_messages(main_module):
    from db import async_session
    from models import Message

    async def _select():
        async with async_session() as session:
            result = await session.execute(select(Message).order_by(Message.created_at))
            return result.scalars().all()

    return asyncio.run(_select())


def test_chat_persists_user_and_assistant_messages(main_module):
    with TestClient(main_module.app) as client:
        main_module.app.state.agent_loop = FakeAgentLoop("first answer")
        response = client.post("/chat", json={"message": "hello"})
        assert response.status_code == 200

    messages = _query_messages(main_module)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "hello"
    assert messages[1].role == "model"
    assert messages[1].content == "first answer"


def test_restart_reloads_history(main_module, capsys):
    with TestClient(main_module.app) as client:  # noqa: F841 - context manager runs lifespan
        main_module.app.state.agent_loop = FakeAgentLoop("resume-check")
        # A fresh lifespan startup must have seeded contents from Test 1's
        # persisted rows, not started empty.
        assert len(main_module.app.state.contents) == 2

    captured = capsys.readouterr()
    assert re.search(r"Resumed conversation .*: 2 prior messages loaded", captured.out)


def test_chat_returns_final_text(main_module):
    with TestClient(main_module.app) as client:
        main_module.app.state.agent_loop = FakeAgentLoop("the final answer text")
        response = client.post("/chat", json={"message": "hi again"})

    assert response.json() == {"final_text": "the final answer text"}
