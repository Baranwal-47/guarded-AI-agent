"""Sanitizer unit tests (offline) + live MCP discovery/round-trip script.

Run `uv run pytest test_discovery.py -k sanitize -x -q` for the offline
sanitizer tests, or `uv run python test_discovery.py` for the live discovery
script that connects to both real MCP servers (network + subprocess required).
"""

import asyncio
import copy
import logging
import sys

from schema_sanitizer import sanitize_schema

DIRTY_SCHEMA = {
    "type": "object",
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Dirty",
    "additionalProperties": False,
    "description": "top level",
    "properties": {
        "path": {
            "type": "string",
            "pattern": "^[a-z]+$",
            "default": "foo",
            "description": "a path",
        },
        "nested": {
            "type": "object",
            "additionalProperties": False,
            "propertyNames": {"pattern": "^[a-z]+$"},
            "properties": {
                "inner": {
                    "oneOf": [{"type": "string"}, {"type": "number"}],
                },
            },
        },
    },
    "items": {
        "type": "string",
        "default": "x",
    },
    "required": ["path"],
    "enum": ["a", "b"],
}


def test_sanitize_removes_unsupported_keys_recursively():
    clean = sanitize_schema(DIRTY_SCHEMA)
    assert "$schema" not in clean
    assert "title" not in clean
    assert "additionalProperties" not in clean
    assert "pattern" not in clean["properties"]["path"]
    assert "default" not in clean["properties"]["path"]
    assert "additionalProperties" not in clean["properties"]["nested"]
    assert "propertyNames" not in clean["properties"]["nested"]
    assert "default" not in clean["items"]


def test_sanitize_renames_oneof_to_anyof():
    clean = sanitize_schema(DIRTY_SCHEMA)
    inner = clean["properties"]["nested"]["properties"]["inner"]
    assert "oneOf" not in inner
    assert inner["anyOf"] == [{"type": "string"}, {"type": "number"}]


def test_sanitize_preserves_supported_keys():
    clean = sanitize_schema(DIRTY_SCHEMA)
    assert clean["type"] == "object"
    assert clean["required"] == ["path"]
    assert clean["enum"] == ["a", "b"]
    assert clean["description"] == "top level"
    assert clean["properties"]["path"]["type"] == "string"
    assert clean["properties"]["path"]["description"] == "a path"
    assert clean["items"]["type"] == "string"


def test_sanitize_does_not_mutate_input():
    original = copy.deepcopy(DIRTY_SCHEMA)
    sanitize_schema(DIRTY_SCHEMA)
    assert DIRTY_SCHEMA == original


def test_sanitize_warns_on_stripped_pattern_and_default(caplog):
    with caplog.at_level(logging.WARNING):
        sanitize_schema(DIRTY_SCHEMA)
    messages = "\n".join(r.message for r in caplog.records)
    assert "pattern" in messages
    assert "default" in messages


async def main() -> int:
    """Live discovery + round-trip script: `uv run python test_discovery.py`.

    Connects to both real MCP servers, proves the tool->server registry is
    built entirely from live tools/list (no hardcoded names), proves
    server_for() resolves a known tool, round-trips one real call, and
    confirms a path-escaping call returns a structured error, not an
    exception.
    """
    from mcp_manager import MCPManager

    manager = MCPManager()
    try:
        await manager.connect_all()

        tools = manager.list_all_tools()
        tool_names = sorted(t["name"] for t in tools)
        print(f"[discover] {len(tool_names)} tools from both servers: {tool_names}")

        sandbox_expected = {"list_files", "read_file", "write_file", "move_file", "delete_file"}
        discovered_sandbox = {n for n in tool_names if n in sandbox_expected}
        if discovered_sandbox != sandbox_expected:
            print(f"FAIL: expected sandbox tools {sandbox_expected}, got {discovered_sandbox}")
            return 1
        context7_tools = {n for n in tool_names if n not in sandbox_expected}
        if not context7_tools:
            print("FAIL: no Context7 tools discovered")
            return 1
        print(f"PASS: sandbox tools {sorted(discovered_sandbox)} + Context7 tools {sorted(context7_tools)}")

        server = manager.server_for("read_file")
        if server != "sandbox":
            print(f"FAIL: server_for('read_file') returned {server!r}, expected 'sandbox'")
            return 1
        print("PASS: server_for('read_file') == 'sandbox' (resolved from live registry)")

        ok_result = await manager.call("read_file", {"path": "notes.txt"})
        if not ok_result.ok:
            print(f"FAIL: call('read_file', notes.txt) returned ok=False: {ok_result.error}")
            return 1
        print(f"PASS: call('read_file', notes.txt) -> ok=True, content={ok_result.content!r}")

        err_result = await manager.call("read_file", {"path": "../server.py"})
        if err_result.ok or not err_result.error or "outside the sandbox root" not in err_result.error:
            print(f"FAIL: escaping call did not return a structured error: {err_result!r}")
            return 1
        print(f"PASS: call('read_file', ../server.py) -> ok=False, error={err_result.error!r}")
    finally:
        await manager.aclose()

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
