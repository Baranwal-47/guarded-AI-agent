import { useCallback, useEffect, useState } from "react";
import { useWebSocketContext } from "../ws/WebSocketContext";
import { api } from "../api/client";
import type { ApprovalRequest } from "../api/types";

export function ApprovalsPage() {
  const { subscribe, onReconnect } = useWebSocketContext();
  const [pending, setPending] = useState<ApprovalRequest[]>([]);
  const [staleMessage, setStaleMessage] = useState<string | null>(null);

  const fetchPending = useCallback(async () => {
    try {
      const rows = await api.get<ApprovalRequest[]>("/approvals?status=pending");
      setPending(rows);
    } catch {
      // Best-effort re-fetch; leave the existing list in place on failure.
    }
  }, []);

  useEffect(() => {
    fetchPending();
  }, [fetchPending]);

  // Reconnect never drops or duplicates a pending item (Pitfall 2/5) — the
  // GET response fully replaces local state rather than merging.
  useEffect(() => {
    const unsub = onReconnect(() => {
      fetchPending();
    });
    return unsub;
  }, [onReconnect, fetchPending]);

  useEffect(() => {
    const unsub = subscribe((event) => {
      switch (event.type) {
        case "approval_required":
          setPending((prev) => {
            if (prev.some((p) => p.id === event.request_id)) return prev;
            return [
              {
                id: event.request_id,
                tool_name: event.tool_name,
                arguments: event.arguments,
                reason: event.reason,
                status: "PENDING",
                decided_by: null,
                created_at: new Date().toISOString(),
                decided_at: null,
              },
              ...prev,
            ];
          });
          break;
        case "approval_granted":
        case "approval_rejected":
          setPending((prev) => prev.filter((p) => p.id !== event.request_id));
          break;
        default:
          break;
      }
    });
    return unsub;
  }, [subscribe]);

  async function decide(id: string, decision: "approve" | "reject") {
    setStaleMessage(null);
    let ok = true;
    try {
      const res = await api.post<{ ok: boolean }>(`/approvals/${id}`, { decision });
      ok = res.ok;
    } catch {
      ok = false;
    }
    if (!ok) {
      setStaleMessage("This request was already resolved.");
    }
    // Whether the decision won the race or arrived stale, this request is no
    // longer actionable from this client — drop the row either way.
    setPending((prev) => prev.filter((p) => p.id !== id));
  }

  return (
    <div>
      <h1 className="text-xl font-semibold text-zinc-50">Approvals</h1>

      {staleMessage && <p className="mt-2 text-sm text-amber-500">{staleMessage}</p>}

      {pending.length === 0 ? (
        <div className="mt-8 text-center">
          <h2 className="text-base font-semibold text-zinc-50">No pending approvals</h2>
          <p className="mt-1 text-sm text-zinc-400">Tool calls requiring approval will appear here in real time.</p>
        </div>
      ) : (
        <div className="mt-4 flex flex-col gap-3">
          {pending.map((p) => (
            <div key={p.id} className="rounded border border-zinc-800 bg-zinc-900 px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <span className="font-mono text-sm text-zinc-50">{p.tool_name}</span>
                  <span className="ml-2 text-xs text-zinc-500">{p.created_at}</span>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => decide(p.id, "approve")}
                    className="rounded bg-blue-500 px-3 py-1 text-xs font-semibold text-white"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => decide(p.id, "reject")}
                    className="rounded bg-red-500 px-3 py-1 text-xs font-semibold text-white"
                  >
                    Reject
                  </button>
                </div>
              </div>
              {p.reason && <p className="mt-1 text-xs text-zinc-400">{p.reason}</p>}
              {Object.keys(p.arguments).length > 0 && (
                <pre className="mt-1 whitespace-pre-wrap font-mono text-xs text-zinc-500">
                  {JSON.stringify(p.arguments)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
