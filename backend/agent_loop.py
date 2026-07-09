"""Agent Loop — capped ReAct step loop routing every tool call through the Gateway.

Holds only the Gateway (execute capability) and a read-only `ToolCatalog`
facade (schema/registry access) — it never holds a raw `MCPManager`
reference, never imports `mcp_manager`, and never invokes the manager's
privileged execute method directly (Anti-Pattern 2, ARCHITECTURE.md
Pattern 1). Every Gemini function_call is
routed through `gateway.execute_tool()`, which always returns the same
`ToolResult`-shaped object regardless of ALLOW/DENY/REQUIRE_APPROVAL, so the
loop serializes the result identically with no per-branch handling.
"""

from google.genai import types


class ToolCatalog:
    """Read-only facade over an MCP manager — the ONLY thing AgentLoop is allowed to hold.

    Exposes exactly `list_all_tools()` and `server_for()`; deliberately has no
    execute/invoke capability and never re-exposes the underlying manager as
    a public attribute. This is what makes "the agent loop cannot reach MCP
    execution" true in code, not just by convention (Anti-Pattern 2).
    """

    def __init__(self, mcp_manager) -> None:
        self._mcp_manager = mcp_manager

    def list_all_tools(self) -> list[dict]:
        return self._mcp_manager.list_all_tools()

    def server_for(self, tool_name: str) -> str:
        return self._mcp_manager.server_for(tool_name)


class AgentLoop:
    """Sends the conversation to Gemini, routes every proposed tool call through
    the Gateway, appends results, and repeats until Gemini returns a final
    answer or `max_steps` is reached (D-11, AGENT-01)."""

    def __init__(self, gemini_client, gateway, tool_provider: ToolCatalog, max_steps: int = 10) -> None:
        self.gemini_client = gemini_client
        self.gateway = gateway
        self.tool_provider = tool_provider
        self.max_steps = max_steps

    async def run_turn(self, contents: list, conversation_id: str, token_usage: int) -> tuple[str, list]:
        """Run one user turn to completion. Returns (final_text, updated_contents)."""
        for step in range(1, self.max_steps + 1):
            tool = self.gemini_client.build_tools(self.tool_provider.list_all_tools())
            response = self.gemini_client.generate(contents, tool)
            print(f"[STEP {step}]")

            calls = self.gemini_client.function_calls(response)
            # Always persist the model's own turn, whether it's a tool call or a
            # final text answer — omitting it here left back-to-back "user" turns
            # in history with no assistant turn between them, which confused
            # later generate() calls (bug found during Task 3 live verification).
            contents.append(response.candidates[0].content)
            if not calls:
                return self.gemini_client.text(response), contents

            response_parts = []
            for call in calls:
                print(f"[TOOL] {call.name} args={call.args!r}")
                server_name = self.tool_provider.server_for(call.name)
                result = await self.gateway.execute_tool(
                    call.name, server_name, call.args, conversation_id, token_usage
                )
                # Uniform serialization: the Gateway returns the same ToolResult
                # shape for ALLOW/DENY/REQUIRE_APPROVAL, so no branch-specific
                # handling is needed here.
                result_dict = {"ok": result.ok, "content": result.content, "error": result.error}
                response_parts.append(self.gemini_client.function_response_part(call.name, result_dict))

            contents.append(types.Content(role="user", parts=response_parts))

        # Step cap reached without a final answer — force one last generate so
        # the turn always terminates (D-11, AGENT-01).
        tool = self.gemini_client.build_tools(self.tool_provider.list_all_tools())
        final_response = self.gemini_client.generate(contents, tool)
        contents.append(final_response.candidates[0].content)
        final_text = self.gemini_client.text(final_response) or "[capped: max steps reached without a final answer]"
        return final_text, contents
