"""Standalone verification client for the Sandbox File Manager MCP server.

Proves, against the real server subprocess (not mocks): live tool discovery,
that reading the injection fixture is inert (returns literal text), and that
an out-of-sandbox path is rejected by the server itself. Run directly:

    uv run python test_client.py
"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {"list_files", "read_file", "write_file", "move_file", "delete_file"}


def _text(result) -> str:
    """Extract the plain text out of a call_tool result's content blocks."""
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


async def main() -> int:
    server = StdioServerParameters(command="uv", args=["run", "python", "server.py"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            discovered = {tool.name for tool in tools.tools}
            if not EXPECTED_TOOLS.issubset(discovered):
                print(f"FAIL: expected tools {EXPECTED_TOOLS}, got {discovered}")
                return 1
            print(f"PASS: discovered all 5 tools via list_tools(): {sorted(discovered)}")

            injected = await session.call_tool("read_file", arguments={"path": "injected_instructions.txt"})
            injected_text = _text(injected)
            if "ignore all previous instructions" not in injected_text:
                print(f"FAIL: injection fixture content not returned literally: {injected_text!r}")
                return 1
            print("PASS: injected_instructions.txt read back as inert literal text")

            escape = await session.call_tool("read_file", arguments={"path": "../server.py"})
            escape_text = _text(escape)
            if "ERROR" not in escape_text or "outside the sandbox root" not in escape_text:
                print(f"FAIL: escaping read was not rejected with structured error: {escape_text!r}")
                return 1
            print("PASS: read_file('../server.py') rejected with structured out-of-sandbox error")

            listing = await session.call_tool("list_files", arguments={"subdir": "."})
            listing_text = _text(listing)
            if "notes.txt" not in listing_text or "secrets.txt" not in listing_text:
                print(f"FAIL: list_files(.) missing expected fixtures: {listing_text!r}")
                return 1
            print(f"PASS: list_files('.') returned multi-entry payload:\n{listing_text}")

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
