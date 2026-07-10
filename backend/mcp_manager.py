"""MCP Manager — the only module that connects to and discovers MCP servers.

Owns dual-transport connection (stdio for the Sandbox File Manager, Streamable
HTTP for Context7) behind one `ClientSession` interface, builds the
`tool_name -> server_name` registry purely from live `tools/list` responses
(MCP-02 — no hardcoded tool lists anywhere except the idempotent-retry
allowlist below, which is retry *behavior*, not the registry itself), and
exposes the one privileged `call()` method the Gateway (01-04) uses to
actually invoke a tool. `agent_loop.py` (01-05) may hold this manager for
read-only schema/registry access (`list_all_tools`, `server_for`) but must
never call `call()` directly — only the Gateway does (ARCHITECTURE.md Pattern
1/Anti-Pattern 2).
"""

import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from config import get_settings
from schema_sanitizer import sanitize_schema

logger = logging.getLogger(__name__)

_SANDBOX_SERVER_DIR = (Path(__file__).parent.parent / "mcp-servers" / "sandbox-file-manager").resolve()
_CONTEXT7_URL = "https://mcp.context7.com/mcp"

# Retry-once allowlist: read-only/idempotent tools only (MCP-04). Never
# write_file/move_file/delete_file — a retried write could double-apply.
_IDEMPOTENT_TOOLS = frozenset({"list_files", "read_file", "resolve-library-id", "query-docs"})

_CALL_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ToolResult:
    """THE single result shape call() returns; the Gateway (01-04) re-returns this in every branch."""

    ok: bool
    content: Any
    error: str | None


def _text_content(result) -> str:
    """Extract plain text from an MCP CallToolResult's content blocks."""
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


@dataclass(frozen=True)
class ServerSpec:
    """One MCP server to connect to. Adding a server = appending one ServerSpec
    to _default_server_specs() (or passing a custom list to MCPManager()) - no
    other code changes, as long as its transport is one of the two already
    supported below. A genuinely new transport type needs one new branch in
    `_connect_transport`, same as any MCP client would."""

    name: str
    transport: str  # "stdio" | "streamable_http"
    stdio_params: StdioServerParameters | None = None
    http_url: str | None = None


def _default_server_specs() -> list[ServerSpec]:
    return [
        ServerSpec(
            name="sandbox",
            transport="stdio",
            stdio_params=StdioServerParameters(
                command="uv", args=["run", "python", "server.py"], cwd=str(_SANDBOX_SERVER_DIR)
            ),
        ),
        ServerSpec(name="context7", transport="streamable_http", http_url=_CONTEXT7_URL),
    ]


class MCPManager:
    """Connects to every configured MCP server, builds the live tool registry, executes calls."""

    def __init__(self, server_specs: list[ServerSpec] | None = None) -> None:
        self._specs = server_specs or _default_server_specs()
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tool_to_server: dict[str, str] = {}
        self._tool_schemas: dict[str, dict] = {}
        self._tool_descriptions: dict[str, str] = {}

    async def _connect_transport(self, spec: ServerSpec, settings) -> ClientSession:
        """Transport-adapter dispatch - config in, live ClientSession out."""
        if spec.transport == "stdio":
            read, write = await self._stack.enter_async_context(stdio_client(spec.stdio_params))
        elif spec.transport == "streamable_http":
            headers = {"CONTEXT7_API_KEY": settings.context7_api_key} if settings.context7_api_key else None
            http_client = create_mcp_http_client(headers=headers)
            read, write, _get_session_id = await self._stack.enter_async_context(
                streamable_http_client(spec.http_url, http_client=http_client)
            )
        else:
            raise ValueError(f"unknown transport '{spec.transport}' for server '{spec.name}'")
        return await self._stack.enter_async_context(ClientSession(read, write))

    async def connect_all(self) -> None:
        """Connect every configured server, discover its tools, and populate the
        registry. Call once at startup."""
        loop_name = type(asyncio.get_event_loop()).__name__
        print(f"[MCP] event loop: {loop_name}", file=sys.stderr)  # Pitfall 1: confirm ProactorEventLoop on Windows

        settings = get_settings()
        for spec in self._specs:
            session = await self._connect_transport(spec, settings)
            await session.initialize()
            self._sessions[spec.name] = session

        for server_name, session in self._sessions.items():
            tools = await session.list_tools()
            for tool in tools.tools:
                self._tool_to_server[tool.name] = server_name
                self._tool_schemas[tool.name] = sanitize_schema(tool.inputSchema)
                self._tool_descriptions[tool.name] = tool.description or ""

    def list_all_tools(self) -> list[dict]:
        """Return every discovered tool's Gemini-ready declaration. Read-only, safe to hand around."""
        return [
            {
                "name": name,
                "description": self._tool_descriptions[name],
                "server_name": server_name,
                "parameters_json_schema": self._tool_schemas[name],
            }
            for name, server_name in self._tool_to_server.items()
        ]

    def server_for(self, tool_name: str) -> str:
        """Resolve a discovered tool to its owning server. Raises KeyError for an unknown tool."""
        return self._tool_to_server[tool_name]

    async def call(self, tool_name: str, arguments: dict) -> ToolResult:
        """THE privileged method — only the Gateway calls this. Never raises; always returns a ToolResult."""
        server_name = self._tool_to_server.get(tool_name)
        if server_name is None:
            return ToolResult(ok=False, content=None, error=f"unknown tool '{tool_name}'")

        session = self._sessions[server_name]
        retries = 2 if tool_name in _IDEMPOTENT_TOOLS else 1
        transport_error: str | None = None

        for attempt in range(1, retries + 1):
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments), timeout=_CALL_TIMEOUT_SECONDS
                )
            except TimeoutError:
                transport_error = f"tool '{tool_name}' timed out after {_CALL_TIMEOUT_SECONDS}s"
            except Exception as exc:  # noqa: BLE001 - never let a raw exception reach the caller (MCP-04)
                transport_error = f"tool '{tool_name}' failed: {exc}"
            else:
                text = _text_content(result)
                if getattr(result, "isError", False) or text.startswith("ERROR:"):
                    return ToolResult(ok=False, content=None, error=text)
                return ToolResult(ok=True, content=text, error=None)

            logger.warning("call(%s) attempt %d/%d failed: %s", tool_name, attempt, retries, transport_error)

        return ToolResult(ok=False, content=None, error=transport_error)

    async def aclose(self) -> None:
        """Terminate the stdio subprocess and close every session (Pitfall 3 — no orphaned processes)."""
        await self._stack.aclose()
