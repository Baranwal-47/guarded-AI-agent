"""In-process wake-up registry for pending REQUIRE_APPROVAL tool calls.

This dict[str, asyncio.Future] is a pure wake-up signal, NEVER the race
arbiter — the conditional `UPDATE approval_requests ... WHERE
status='PENDING'` in gateway.try_decide() is the sole source of truth for
"who won" (POST /approvals/{id} vs. the 5-minute auto-deny timer). wake()
only fires after a caller has already confirmed it won that DB-level race
(RESEARCH Pattern 1/2, Pitfall 2).

Scoped to the app/process lifetime; constructed once in main.py's
composition root and shared between the gateway and the /approvals route
(same instance, not a module-level global — see PATTERNS.md).
"""

import asyncio


class ApprovalManager:
    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}

    def register(self, request_id: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        return fut

    def wake(self, request_id: str, decision: str) -> None:
        """Best-effort wake-up. Safe to call even if already resolved or
        unknown — the DB conditional UPDATE (not this dict) is the actual
        race arbiter."""
        fut = self._pending.get(request_id)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    def discard(self, request_id: str) -> None:
        self._pending.pop(request_id, None)
