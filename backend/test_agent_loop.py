"""Tests for AgentLoop: the LLM is untrusted even though Gemini is normally
constrained to declared tools - a hallucinated/unknown tool name must never
crash the turn (Stage 5 gap fixed after the ChatGPT stage-by-stage audit).

Uses plain asyncio.run(), matching test_gateway.py's convention - no new
test dependency needed.
"""

import asyncio

from google.genai import types

from agent_loop import AgentLoop, ToolCatalog
from mcp_manager import ToolResult


class FakeCall:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.args = args


class FakeCandidate:
    def __init__(self) -> None:
        self.content = object()  # opaque sentinel; agent_loop only re-appends it


class FakeResponse:
    def __init__(self, calls: list, text: str | None) -> None:
        self.calls = calls
        self.text = text
        self.candidates = [FakeCandidate()]


class FakeGeminiClient:
    """Returns a scripted sequence of responses, one per generate() call."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.response_parts_fed: list[tuple[str, dict]] = []

    def build_tools(self, tools: list[dict]):
        return "fake-tool-decl"

    def generate(self, contents, tool):
        return self._responses.pop(0)

    def function_calls(self, response: FakeResponse) -> list:
        return response.calls

    def text(self, response: FakeResponse) -> str | None:
        return response.text

    def function_response_part(self, name: str, response: dict):
        self.response_parts_fed.append((name, response))
        return types.Part.from_function_response(name=name, response=response)


class FakeMCPManagerForCatalog:
    """Backs ToolCatalog with a fixed name -> server_name registry."""

    def __init__(self, known_tools: dict[str, str]) -> None:
        self._known = known_tools

    def list_all_tools(self) -> list[dict]:
        return [
            {"name": n, "description": "", "server_name": s, "parameters_json_schema": {}}
            for n, s in self._known.items()
        ]

    def server_for(self, tool_name: str) -> str:
        return self._known[tool_name]  # raises KeyError for an unregistered tool


class FakeGateway:
    """Records execute_tool calls; must never be called for an unknown tool."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def execute_tool(self, tool_name, server_name, arguments, conversation_id, token_usage) -> ToolResult:
        self.calls.append((tool_name, server_name, arguments))
        return ToolResult(ok=True, content="fake-content", error=None)


def test_unknown_tool_call_does_not_crash_and_feeds_back_synthesized_error():
    catalog = ToolCatalog(FakeMCPManagerForCatalog({"read_file": "sandbox"}))
    gateway = FakeGateway()

    unknown_call = FakeCall("nonexistent_tool", {"foo": "bar"})
    gemini = FakeGeminiClient(
        [
            FakeResponse(calls=[unknown_call], text=None),
            FakeResponse(calls=[], text="recovered answer"),
        ]
    )
    loop = AgentLoop(gemini, gateway, catalog, max_steps=5)

    final_text, _contents = asyncio.run(loop.run_turn(contents=[], conversation_id="c1", token_usage=0))

    assert final_text == "recovered answer"
    assert gateway.calls == []  # unknown tool never reaches the gateway/MCP layer
    assert gemini.response_parts_fed == [
        (
            "nonexistent_tool",
            {"ok": False, "content": None, "error": "UNKNOWN_TOOL: 'nonexistent_tool' is not a registered tool"},
        )
    ]


def test_known_tool_call_still_routes_through_gateway_normally():
    catalog = ToolCatalog(FakeMCPManagerForCatalog({"read_file": "sandbox"}))
    gateway = FakeGateway()

    known_call = FakeCall("read_file", {"path": "notes.txt"})
    gemini = FakeGeminiClient(
        [
            FakeResponse(calls=[known_call], text=None),
            FakeResponse(calls=[], text="final answer"),
        ]
    )
    loop = AgentLoop(gemini, gateway, catalog, max_steps=5)

    final_text, _contents = asyncio.run(loop.run_turn(contents=[], conversation_id="c1", token_usage=0))

    assert final_text == "final answer"
    assert gateway.calls == [("read_file", "sandbox", {"path": "notes.txt"})]
    assert gemini.response_parts_fed == [("read_file", {"ok": True, "content": "fake-content", "error": None})]


if __name__ == "__main__":
    test_unknown_tool_call_does_not_crash_and_feeds_back_synthesized_error()
    test_known_tool_call_still_routes_through_gateway_normally()
    print("ALL PASS")
