"""Tool Execution Gateway — the single choke point for every MCP tool call.

No code path other than this module may call `MCPManager.call()`. Every
requested tool call is gated by the Policy Engine on STRUCTURED facts only
(never model free text — Pitfall 5); on REQUIRE_APPROVAL it persists an
`approval_requests` row and blocks on an `asyncio.Future` until a human
(`POST /approvals/{id}`) or a per-approval 5-minute timer resolves it,
fail-closed on anything but an explicit "approve" (D-01/D-02, POLICY-05,
APPROVAL-01/02/03). Every branch returns the SAME `ToolResult` shape
imported from `mcp_manager`, so the agent loop (01-05) needs zero
per-branch handling.

The conditional `UPDATE ... WHERE status='PENDING'` in `try_decide()` is
the sole race arbiter between the HTTP handler, the timeout task, and
startup reconciliation — `ApprovalManager.wake()` is only ever called by
whichever caller's `try_decide()` returned True (RESEARCH Pattern 2).
"""

import asyncio
from uuid import uuid4

from approval_manager import ApprovalManager
from mcp_manager import MCPManager, ToolResult
from models import ApprovalRequest
from policy_engine import Action, PolicyContext, evaluate, load_rules
from sqlalchemy import func, update


async def try_decide(session, request_id: str, new_status: str, decided_by: str) -> bool:
    """Conditional UPDATE ... WHERE status='PENDING' — the single race
    arbiter shared by the HTTP handler, the auto-deny timer, and startup
    reconciliation. Returns True only for whichever caller's UPDATE
    actually matched the row (rowcount == 1); a duplicate/late caller gets
    False (rowcount == 0), a no-op rather than an exception (APPROVAL-02)."""
    result = await session.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == request_id, ApprovalRequest.status == "PENDING")
        .values(status=new_status, decided_by=decided_by, decided_at=func.now())
    )
    await session.commit()
    return result.rowcount == 1


async def reconcile_pending_approvals(session) -> int:
    """Startup pass (APPROVAL-03, fail-closed): any `approval_requests` row
    still PENDING at process start had its in-process Future/timer task
    destroyed by the restart — nothing else will ever resolve it. Denies
    every orphan, never leaves it PENDING or silently approves it. Returns
    the number of rows reconciled."""
    result = await session.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.status == "PENDING")
        .values(status="DENIED", decided_by="system-restart", decided_at=func.now())
    )
    await session.commit()
    return result.rowcount


class ToolExecutionGateway:
    def __init__(
        self,
        mcp_manager: MCPManager,
        session_factory,
        approval_manager: ApprovalManager,
        timeout_seconds: int = 300,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.session_factory = session_factory
        self.approval_manager = approval_manager
        self.timeout_seconds = timeout_seconds

    async def _persist_approval_request(self, request_id: str, tool_name: str, arguments: dict, reason: str) -> None:
        async with self.session_factory() as session:
            session.add(
                ApprovalRequest(id=request_id, tool_name=tool_name, arguments=arguments, reason=reason, status="PENDING")
            )
            await session.commit()

    async def _auto_deny(self, request_id: str, timeout: float) -> None:
        await asyncio.sleep(timeout)
        async with self.session_factory() as session:
            won = await try_decide(session, request_id, "DENIED", "system-timeout")
        if won:
            self.approval_manager.wake(request_id, "reject")

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
            async with self.session_factory() as session:
                rules = await load_rules(session)  # fresh read every call, no cache
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
            request_id = str(uuid4())
            print(f"[APPROVAL] pending id={request_id} tool={tool_name!r} args={arguments!r} reason={decision.reason!r}")
            await self._persist_approval_request(request_id, tool_name, arguments, decision.reason)
            fut = self.approval_manager.register(request_id)
            timer_task = asyncio.create_task(self._auto_deny(request_id, self.timeout_seconds))
            try:
                outcome = await fut
            finally:
                timer_task.cancel()  # no-op if the timer already fired and exited
                self.approval_manager.discard(request_id)
            if outcome != "approve":
                reason = "denied (human or timeout)"
                print(f"[RESULT] {reason}")
                return ToolResult(ok=False, content=None, error=reason)

        result = await self.mcp_manager.call(tool_name, arguments)
        print(f"[RESULT] ok={result.ok} error={result.error!r}")
        return result
