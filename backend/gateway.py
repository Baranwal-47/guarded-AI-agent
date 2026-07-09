"""Tool Execution Gateway — the single choke point for every MCP tool call.

No code path other than this module may call `MCPManager.call()`. Every
requested tool call is gated by the Policy Engine on STRUCTURED facts only
(never model free text — Pitfall 5); on REQUIRE_APPROVAL it blocks
synchronously on a terminal y/n prompt (D-01) and fails closed on any
non-affirmative/invalid input (D-02, POLICY-05). Every branch returns the
SAME `ToolResult` shape imported from `mcp_manager`, so the agent loop
(01-05) needs zero per-branch handling.
"""

from mcp_manager import MCPManager, ToolResult
from policy_engine import Action, PolicyContext, evaluate, load_rules


class ToolExecutionGateway:
    def __init__(self, mcp_manager: MCPManager, rules_path: str) -> None:
        self.mcp_manager = mcp_manager
        self.rules_path = rules_path

    async def execute_tool(
        self,
        tool_name: str,
        server_name: str,
        arguments: dict,
        conversation_id: str,
        token_usage: int,
    ) -> ToolResult:
        ctx = PolicyContext(
            tool_name=tool_name,
            server_name=server_name,
            arguments=arguments,
            conversation_id=conversation_id,
            current_token_usage=token_usage,
        )

        try:
            rules = load_rules(self.rules_path)  # fresh read every call, no cache
            decision = evaluate(ctx, rules)
        except Exception as exc:  # noqa: BLE001 - policy engine failure must fail closed, never ALLOW
            reason = f"policy engine error (fail-closed DENY): {exc}"
            print(f"[POLICY] DENY reason={reason!r} matched_rule_ids=[]")
            return ToolResult(ok=False, content=None, error=reason)

        print(
            f"[POLICY] {decision.action.value} reason={decision.reason!r} "
            f"matched_rule_ids={decision.matched_rule_ids}"
        )

        if decision.action is Action.DENY:
            return ToolResult(ok=False, content=None, error=decision.reason)

        if decision.action is Action.REQUIRE_APPROVAL:
            print(f"[APPROVAL] pending tool={tool_name!r} args={arguments!r} reason={decision.reason!r}")
            answer = input("Approve this tool call? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                reason = "rejected by approver (fail-closed)"
                print(f"[RESULT] {reason}")
                return ToolResult(ok=False, content=None, error=reason)

        result = await self.mcp_manager.call(tool_name, arguments)
        print(f"[RESULT] ok={result.ok} error={result.error!r}")
        return result
