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
from typing import Awaitable, Callable
from uuid import uuid4

from approval_manager import ApprovalManager
from mcp_manager import MCPManager, ToolResult
from models import ApprovalRequest, AuditLog, ToolExecution
from policy_engine import Action, PolicyContext, evaluate, load_rules
from sqlalchemy import func, update


async def _default_broadcast(event: dict) -> None:  # ponytail: no-op default so callers/tests without a ws_manager keep working
    return None


# SEC-02/D-06/D-07: case-insensitive substring scan against read/external-content
# tool output only. Logging-only signal — see scan_for_prompt_injection's docstring
# and policy_engine.PolicyContext's docstring for the invariant this must never cross.
_SUSPICIOUS_PHRASES = (
    "ignore previous instructions",
    "ignore all previous instructions",  # matches SANDBOX-03's actual fixture wording verbatim (Pitfall 1)
    "ignore all prior",
    "disregard the above",
    "you must now",
    "new instructions:",
)

_SCANNED_TOOLS = {"read_file", "list_files", "query-docs", "resolve-library-id"}  # D-07


def scan_for_prompt_injection(tool_name: str, content: str | None) -> bool:
    """Logging-only heuristic (SEC-02). NEVER pass this result into
    PolicyContext/evaluate() — that would reopen the free-text bypass
    policy_engine.py's own docstring warns against (POLICY-01)."""
    if tool_name not in _SCANNED_TOOLS or not content:
        return False
    lowered = content.lower()
    return any(phrase in lowered for phrase in _SUSPICIOUS_PHRASES)


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
        broadcast: Callable[[dict], Awaitable[None]] = _default_broadcast,
    ) -> None:
        self.mcp_manager = mcp_manager
        self.session_factory = session_factory
        self.approval_manager = approval_manager
        self.timeout_seconds = timeout_seconds
        self.broadcast = broadcast

    async def _audit(self, event: str, detail: dict, flags: str | None = None) -> None:
        """Write one AuditLog row per lifecycle event (D-09 Tab 2) — same
        session_factory pattern as _persist_tool_execution/_persist_approval_request."""
        async with self.session_factory() as session:
            session.add(AuditLog(event=event, detail=detail, flags=flags))
            await session.commit()

    async def _safe_broadcast(self, event: dict) -> None:
        """WebSocketManager.broadcast() is documented to never raise, but this
        is the security boundary — defense-in-depth in case an injected
        broadcast callable violates that contract (T-03-01): a broadcast
        failure must never change execute_tool()'s outcome or block the
        AuditLog write."""
        try:
            await self.broadcast(event)
        except Exception:  # noqa: BLE001 - broadcast failures must never affect the tool call itself
            pass

    async def _persist_approval_request(self, request_id: str, tool_name: str, arguments: dict, reason: str) -> None:
        async with self.session_factory() as session:
            session.add(
                ApprovalRequest(id=request_id, tool_name=tool_name, arguments=arguments, reason=reason, status="PENDING")
            )
            await session.commit()

    async def _persist_tool_execution(
        self,
        *,
        conversation_id: str,
        tool_name: str,
        arguments: dict,
        decision_action: str,
        decision_reason: str,
        matched_rule_ids: list[str],
        result: ToolResult,
        flagged: bool,
    ) -> None:
        """Write the tool_executions row for this branch (and an audit_logs
        entry when flagged) — the durable replacement for the old
        [POLICY]/[RESULT] prints (T-02-12). Called on every branch: DENY,
        approval-denied, and ALLOW/executed."""
        async with self.session_factory() as session:
            session.add(
                ToolExecution(
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    decision_action=decision_action,
                    decision_reason=decision_reason,
                    matched_rule_ids=matched_rule_ids,
                    result_ok=result.ok,
                    result_error=result.error,
                    flagged_prompt_injection=flagged,
                )
            )
            if flagged:
                session.add(
                    AuditLog(
                        event="tool_execution",
                        detail={"tool_name": tool_name, "arguments": arguments},
                        flags="PROMPT_INJECTION_SUSPECTED",
                    )
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

        await self._safe_broadcast(
            {"type": "tool_requested", "tool_name": tool_name, "arguments": arguments, "conversation_id": conversation_id}
        )
        await self._audit(
            "tool_requested", {"tool_name": tool_name, "arguments": arguments, "conversation_id": conversation_id}
        )

        try:
            async with self.session_factory() as session:
                rules = await load_rules(session)  # fresh read every call, no cache
            decision = evaluate(ctx, rules)
        except Exception as exc:  # noqa: BLE001 - policy engine failure must fail closed, never ALLOW
            reason = f"policy engine error (fail-closed DENY): {exc}"
            result = ToolResult(ok=False, content=None, error=reason)
            await self._persist_tool_execution(
                conversation_id=conversation_id,
                tool_name=tool_name,
                arguments=arguments,
                decision_action=Action.DENY.value,
                decision_reason=reason,
                matched_rule_ids=[],
                result=result,
                flagged=False,
            )
            await self._safe_broadcast(
                {
                    "type": "execution_failed",
                    "tool_name": tool_name,
                    "result_ok": False,
                    "result_error": reason,
                    "conversation_id": conversation_id,
                }
            )
            await self._audit(
                "execution_failed",
                {"tool_name": tool_name, "result_ok": False, "result_error": reason, "conversation_id": conversation_id},
            )
            return result

        await self._safe_broadcast(
            {
                "type": "policy_decided",
                "tool_name": tool_name,
                "action": decision.action.value,
                "reason": decision.reason,
                "matched_rule_ids": decision.matched_rule_ids,
                "conversation_id": conversation_id,
            }
        )
        await self._audit(
            "policy_decided",
            {
                "tool_name": tool_name,
                "action": decision.action.value,
                "reason": decision.reason,
                "matched_rule_ids": decision.matched_rule_ids,
                "conversation_id": conversation_id,
            },
        )

        if decision.action is Action.DENY:
            result = ToolResult(ok=False, content=None, error=decision.reason)
            await self._persist_tool_execution(
                conversation_id=conversation_id,
                tool_name=tool_name,
                arguments=arguments,
                decision_action=decision.action.value,
                decision_reason=decision.reason,
                matched_rule_ids=decision.matched_rule_ids,
                result=result,
                flagged=False,
            )
            await self._safe_broadcast(
                {
                    "type": "execution_failed",
                    "tool_name": tool_name,
                    "result_ok": False,
                    "result_error": decision.reason,
                    "conversation_id": conversation_id,
                }
            )
            await self._audit(
                "execution_failed",
                {
                    "tool_name": tool_name,
                    "result_ok": False,
                    "result_error": decision.reason,
                    "conversation_id": conversation_id,
                },
            )
            return result

        if decision.action is Action.REQUIRE_APPROVAL:
            request_id = str(uuid4())
            # Register the Future BEFORE persisting the PENDING row: the row is
            # what makes this request_id decidable (POST /approvals/{id} or the
            # timer can win try_decide()'s race the instant the row commits), so
            # the Future must already exist first — otherwise a decision landing
            # between persist and register calls wake() on an unknown id and is
            # silently dropped (ApprovalManager.wake() no-ops on unknown ids),
            # hanging this request forever. register() is synchronous — no
            # await happens between it and persisting, so there's no window.
            fut = self.approval_manager.register(request_id)
            try:
                await self._persist_approval_request(request_id, tool_name, arguments, decision.reason)
                await self._safe_broadcast(
                    {
                        "type": "approval_required",
                        "request_id": request_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "reason": decision.reason,
                        "conversation_id": conversation_id,
                    }
                )
                await self._audit(
                    "approval_required",
                    {
                        "request_id": request_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "reason": decision.reason,
                        "conversation_id": conversation_id,
                    },
                )
                timer_task = asyncio.create_task(self._auto_deny(request_id, self.timeout_seconds))
                try:
                    outcome = await fut
                finally:
                    timer_task.cancel()  # no-op if the timer already fired and exited
            finally:
                self.approval_manager.discard(request_id)

            approval_event_type = "approval_granted" if outcome == "approve" else "approval_rejected"
            approval_event = {
                "type": approval_event_type,
                "request_id": request_id,
                "tool_name": tool_name,
                "conversation_id": conversation_id,
            }
            await self._safe_broadcast(approval_event)
            await self._audit(approval_event_type, {k: v for k, v in approval_event.items() if k != "type"})

            if outcome != "approve":
                result = ToolResult(ok=False, content=None, error="denied (human or timeout)")
                await self._persist_tool_execution(
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    decision_action=decision.action.value,
                    decision_reason=decision.reason,
                    matched_rule_ids=decision.matched_rule_ids,
                    result=result,
                    flagged=False,
                )
                await self._safe_broadcast(
                    {
                        "type": "execution_failed",
                        "tool_name": tool_name,
                        "result_ok": False,
                        "result_error": result.error,
                        "conversation_id": conversation_id,
                    }
                )
                await self._audit(
                    "execution_failed",
                    {
                        "tool_name": tool_name,
                        "result_ok": False,
                        "result_error": result.error,
                        "conversation_id": conversation_id,
                    },
                )
                return result

        await self._safe_broadcast({"type": "execution_started", "tool_name": tool_name, "conversation_id": conversation_id})
        await self._audit("execution_started", {"tool_name": tool_name, "conversation_id": conversation_id})

        result = await self.mcp_manager.call(tool_name, arguments)
        flagged = scan_for_prompt_injection(tool_name, result.content)
        await self._persist_tool_execution(
            conversation_id=conversation_id,
            tool_name=tool_name,
            arguments=arguments,
            decision_action=decision.action.value,
            decision_reason=decision.reason,
            matched_rule_ids=decision.matched_rule_ids,
            result=result,
            flagged=flagged,
        )
        if result.ok:
            final_event = {"type": "execution_completed", "tool_name": tool_name, "result_ok": True, "conversation_id": conversation_id}
        else:
            final_event = {
                "type": "execution_failed",
                "tool_name": tool_name,
                "result_ok": False,
                "result_error": result.error,
                "conversation_id": conversation_id,
            }
        await self._safe_broadcast(final_event)
        await self._audit(final_event["type"], {k: v for k, v in final_event.items() if k != "type"})
        return result
