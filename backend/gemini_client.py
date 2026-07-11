"""Gemini client — proposes tool calls, never executes them.

Builds `FunctionDeclaration`s from live, already-sanitized MCP tool schemas
(`MCPManager.list_all_tools()`) and calls `generate_content` with automatic
function calling explicitly DISABLED on every call (AGENT-02) — the SDK must
never execute an MCP tool itself. This module only ever builds schemas and
proposes calls; the live MCP transport handle is never passed to it, and
execution only ever happens through `gateway.ToolExecutionGateway` (01-04).
"""

from google import genai
from google.genai import errors, types


class LLMUnavailableError(Exception):
    """The Gemini API itself failed (quota exhausted, rate limit, outage,
    transient network error) — as opposed to the model successfully
    responding. A turn-level failure, never routed through the policy engine
    or Gateway (Pitfall 5): this has nothing to do with tool-call guardrails."""


class GeminiClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def build_tools(self, tools: list[dict]) -> types.Tool:
        """Build one Tool wrapping a FunctionDeclaration per live-discovered MCP tool.

        Rebuild this every turn from `MCPManager.list_all_tools()` — never
        hardcode tool names here (MCP-02).
        """
        declarations = [
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters_json_schema=tool["parameters_json_schema"],
            )
            for tool in tools
        ]
        return types.Tool(function_declarations=declarations)

    def generate(self, contents: list, tool: types.Tool) -> types.GenerateContentResponse:
        """Propose the next turn. Automatic function calling is ALWAYS disabled (AGENT-02) —
        this method never receives an MCP transport handle; the gateway is the only executor.

        Raises LLMUnavailableError (never the raw SDK exception) on any API-level
        failure (quota/rate-limit/outage) so callers have one exception type to
        handle regardless of which of Google's error subclasses fired."""
        try:
            return self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    tools=[tool],
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.APIError as exc:
            raise LLMUnavailableError(f"Gemini API error {exc.code} ({exc.status}): {exc.message}") from exc

    @staticmethod
    def function_calls(response: types.GenerateContentResponse) -> list:
        """Each item has `.name` and `.args`. Empty list if the model returned no tool call."""
        return response.function_calls or []

    @staticmethod
    def text(response: types.GenerateContentResponse) -> str | None:
        return response.text

    @staticmethod
    def total_tokens(response: types.GenerateContentResponse) -> int:
        """Tokens billed for this single generate_content call (prompt + candidates),
        used to accumulate PolicyContext.current_token_usage per conversation."""
        usage = response.usage_metadata
        return usage.total_token_count if usage and usage.total_token_count else 0

    @staticmethod
    def function_response_part(name: str, response: dict) -> types.Part:
        """Build the Part to feed a tool result back to Gemini next turn."""
        return types.Part.from_function_response(name=name, response=response)
