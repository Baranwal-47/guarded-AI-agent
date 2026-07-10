"""In-process WebSocket connection registry for the dashboard's live event feed.

Invariant this class exists to preserve: a broadcast failure (a dead/closed
socket, a slow client) must NEVER affect tool execution — `broadcast()` never
raises. It is a pure fan-out sink, not a race arbiter or a durability
mechanism (AuditLog rows are the durable record; this is best-effort push).

Scoped to the app/process lifetime; constructed once in main.py's composition
root and shared between the gateway and the `/ws` route (same instance, not a
module-level global — see PATTERNS.md "Constructor-injection over
module-level globals").
"""

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    async def broadcast(self, event: dict) -> None:
        """Best-effort fan-out. Never raises — a dead socket is collected
        and discarded, never allowed to propagate into the gateway's
        ALLOW/DENY/REQUIRE_APPROVAL control flow."""
        dead = []
        for ws in list(self._connections):
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001 - any send failure means a dead socket, never a reason to raise
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)
