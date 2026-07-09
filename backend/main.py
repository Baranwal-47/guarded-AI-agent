"""Composition root + terminal REPL.

Wires MCPManager -> Gateway (privileged execute capability) and
MCPManager -> ToolCatalog -> AgentLoop (read-only) — the AgentLoop receives
only the Gateway plus the read-only `ToolCatalog` facade, never the raw
`MCPManager` (ARCHITECTURE.md Pattern 1, Anti-Pattern 2). Runs an `input()`
REPL that persists conversation history across turns and exits on
`exit`/`quit`/Ctrl+C (D-10), always tearing down the stdio subprocess in a
`finally` block (Pitfall 3). Never overrides the asyncio event loop policy —
let `asyncio.run()` use the platform default so stdio subprocess spawning
keeps working on Windows (Pitfall 1).
"""

import asyncio

from agent_loop import AgentLoop, ToolCatalog
from config import get_settings
from gateway import ToolExecutionGateway
from gemini_client import GeminiClient
from google.genai import types
from mcp_manager import MCPManager

_CONVERSATION_ID = "terminal-session"


async def main() -> None:
    settings = get_settings()

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

        tool_names = [tool["name"] for tool in catalog.list_all_tools()]
        print(f"[STARTUP] discovered {len(tool_names)} tools: {tool_names}")

        contents: list = []
        token_usage = 0

        while True:
            try:
                user_input = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[EXIT] goodbye")
                break

            if user_input.lower() in ("exit", "quit"):
                print("[EXIT] goodbye")
                break
            if not user_input:
                continue

            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_input)]))
            final_text, contents = await agent_loop.run_turn(contents, _CONVERSATION_ID, token_usage)
            print(f"[FINAL] {final_text}")
    finally:
        await mcp_manager.aclose()


if __name__ == "__main__":
    asyncio.run(main())
