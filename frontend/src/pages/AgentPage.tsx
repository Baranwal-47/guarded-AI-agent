import { useCallback, useEffect, useReducer, useRef, useState, type FormEvent } from "react";
import { useWebSocketContext } from "../ws/WebSocketContext";
import { api } from "../api/client";
import type { Action, ChatState } from "../api/types";
import { DecisionBadge } from "../components/DecisionBadge";

type ApprovalStatus = "pending" | "granted" | "rejected";

type TranscriptEntry =
  | { kind: "user"; id: string; content: string }
  | { kind: "model"; id: string; content: string }
  | { kind: "tool_requested"; id: string; tool_name: string; arguments: Record<string, unknown> }
  | {
      kind: "policy_decided";
      id: string;
      tool_name: string;
      action: Action;
      reason: string;
      matched_rule_ids: string[];
    }
  | { kind: "execution_result"; id: string; tool_name: string; ok: boolean; error?: string }
  | {
      kind: "approval";
      id: string;
      tool_name: string;
      arguments: Record<string, unknown>;
      reason: string;
      status: ApprovalStatus;
    };

type TranscriptAction =
  | { type: "add"; entry: TranscriptEntry }
  | { type: "upsert_approval"; id: string; patch: Partial<Extract<TranscriptEntry, { kind: "approval" }>> }
  | { type: "reconcile"; entries: TranscriptEntry[] };

function reducer(state: TranscriptEntry[], action: TranscriptAction): TranscriptEntry[] {
  switch (action.type) {
    case "add":
      return [...state, action.entry];
    case "upsert_approval": {
      const idx = state.findIndex((e) => e.kind === "approval" && e.id === action.id);
      if (idx === -1) {
        return [
          ...state,
          {
            kind: "approval",
            id: action.id,
            tool_name: "",
            arguments: {},
            reason: "",
            status: "pending",
            ...action.patch,
          } as TranscriptEntry,
        ];
      }
      const next = [...state];
      next[idx] = { ...next[idx], ...action.patch } as TranscriptEntry;
      return next;
    }
    case "reconcile":
      return action.entries;
    default:
      return state;
  }
}

type Timestamped<T> = T & { created_at: string };

/** Reconciles the authoritative GET /chat/state snapshot into transcript entries (Pitfall 2: replace, don't append).
 *
 * Rebuilds tool_requested/policy_decided/execution_result blocks from
 * `recent_tool_calls` too, not just user/model messages — otherwise every
 * remount (nav away and back, WS reconnect) wiped the visual record of a
 * tool call that already resolved, even though it genuinely ran. */
function entriesFromChatState(state: ChatState): TranscriptEntry[] {
  const messageEntries: Timestamped<TranscriptEntry>[] = state.recent_messages.map((m, i) => ({
    kind: m.role === "user" ? "user" : "model",
    id: `msg-${i}-${m.created_at}`,
    content: m.content,
    created_at: m.created_at,
  }));

  const toolEntries: Timestamped<TranscriptEntry>[] = state.recent_tool_calls.flatMap((t, i) => {
    const base = `tool-${i}-${t.created_at}`;
    const entries: Timestamped<TranscriptEntry>[] = [
      {
        kind: "tool_requested",
        id: `${base}-req`,
        tool_name: t.tool_name,
        arguments: t.arguments,
        created_at: t.created_at,
      },
      {
        kind: "policy_decided",
        id: `${base}-decide`,
        tool_name: t.tool_name,
        action: t.decision_action,
        reason: t.decision_reason,
        matched_rule_ids: t.matched_rule_ids,
        created_at: t.created_at,
      },
    ];
    if (t.result_ok !== null) {
      entries.push({
        kind: "execution_result",
        id: `${base}-result`,
        tool_name: t.tool_name,
        ok: t.result_ok,
        error: t.result_error ?? undefined,
        created_at: t.created_at,
      });
    }
    return entries;
  });

  const approvalEntries: Timestamped<TranscriptEntry>[] = state.pending_approvals.map((a) => ({
    kind: "approval",
    id: a.id,
    tool_name: a.tool_name,
    arguments: a.arguments,
    reason: a.reason,
    status: "pending",
    created_at: a.created_at,
  }));

  return [...messageEntries, ...toolEntries, ...approvalEntries].sort((a, b) =>
    a.created_at.localeCompare(b.created_at)
  );
}

export function AgentPage() {
  const { subscribe, onReconnect } = useWebSocketContext();
  const [transcript, dispatch] = useReducer(reducer, [] as TranscriptEntry[]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [tokenUsage, setTokenUsage] = useState(0);
  const idCounter = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const nextId = () => `e-${idCounter.current++}`;

  const hydrate = useCallback(async () => {
    try {
      const state = await api.get<ChatState>("/chat/state");
      dispatch({ type: "reconcile", entries: entriesFromChatState(state) });
      setTokenUsage(state.token_usage);
    } catch {
      // Best-effort hydration; a failed re-fetch leaves the existing transcript in place.
    } finally {
      setInitialLoading(false);
    }
  }, []);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    const unsubReconnect = onReconnect(() => {
      hydrate();
    });
    return unsubReconnect;
  }, [onReconnect, hydrate]);

  useEffect(() => {
    const unsub = subscribe((event) => {
      switch (event.type) {
        case "tool_requested":
          dispatch({
            type: "add",
            entry: { kind: "tool_requested", id: nextId(), tool_name: event.tool_name, arguments: event.arguments },
          });
          break;
        case "policy_decided":
          dispatch({
            type: "add",
            entry: {
              kind: "policy_decided",
              id: nextId(),
              tool_name: event.tool_name,
              action: event.action,
              reason: event.reason,
              matched_rule_ids: event.matched_rule_ids,
            },
          });
          break;
        case "execution_completed":
          dispatch({
            type: "add",
            entry: { kind: "execution_result", id: nextId(), tool_name: event.tool_name, ok: true },
          });
          break;
        case "execution_failed":
          dispatch({
            type: "add",
            entry: {
              kind: "execution_result",
              id: nextId(),
              tool_name: event.tool_name,
              ok: false,
              error: event.result_error,
            },
          });
          break;
        case "approval_required":
          dispatch({
            type: "upsert_approval",
            id: event.request_id,
            patch: {
              tool_name: event.tool_name,
              arguments: event.arguments,
              reason: event.reason,
              status: "pending",
            },
          });
          break;
        case "approval_granted":
          dispatch({ type: "upsert_approval", id: event.request_id, patch: { status: "granted" } });
          break;
        case "approval_rejected":
          dispatch({ type: "upsert_approval", id: event.request_id, patch: { status: "rejected" } });
          break;
        default:
          break;
      }
    });
    return unsub;
  }, [subscribe]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [transcript.length]);

  async function handleClear() {
    if (sending) return;
    try {
      await api.delete("/chat");
      dispatch({ type: "reconcile", entries: [] });
      setTokenUsage(0);
    } catch {
      setError("Couldn't clear the conversation. Try again.");
    }
  }

  async function handleSend(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setError(null);
    dispatch({ type: "add", entry: { kind: "user", id: nextId(), content: text } });
    setSending(true);
    try {
      const res = await api.post<{ final_text: string }>("/chat", { message: text });
      dispatch({ type: "add", entry: { kind: "model", id: nextId(), content: res.final_text } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong processing that message. Try sending it again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-zinc-50">Agent</h1>
        <div className="flex items-center gap-3">
          <span className="text-xs text-zinc-500">Tokens used: {tokenUsage.toLocaleString()}</span>
          <button
            type="button"
            onClick={handleClear}
            disabled={sending || transcript.length === 0}
            className="rounded border border-zinc-800 px-3 py-1 text-xs text-zinc-400 hover:text-zinc-50 disabled:opacity-50"
          >
            Clear chat
          </button>
        </div>
      </div>

      <div className="mt-4 flex-1 overflow-y-auto rounded bg-zinc-900 p-4">
        {initialLoading ? (
          <div className="mt-8 text-center">
            <p className="text-sm text-zinc-400">Loading conversation…</p>
          </div>
        ) : transcript.length === 0 ? (
          <div className="mt-8 text-center">
            <h2 className="text-base font-semibold text-zinc-50">No messages yet</h2>
            <p className="mt-1 text-sm text-zinc-400">Send a message to start the conversation.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {transcript.map((entry) => (
              <TranscriptRow key={entry.id} entry={entry} />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {error && <p className="mt-2 text-sm text-red-500">{error}</p>}

      <form onSubmit={handleSend} className="mt-4 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Send a message..."
          className="flex-1 rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded bg-blue-500 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  );
}

function TranscriptRow({ entry }: { entry: TranscriptEntry }) {
  switch (entry.kind) {
    case "user":
      return (
        <div className="ml-auto max-w-[75%] rounded bg-blue-500/15 px-3 py-2 text-sm text-zinc-50">
          {entry.content}
        </div>
      );
    case "model":
      return (
        <div className="mr-auto max-w-[75%] rounded bg-zinc-800 px-3 py-2 text-sm text-zinc-50">{entry.content}</div>
      );
    case "tool_requested":
      return (
        <div className="rounded border border-zinc-800 px-3 py-2 text-xs text-zinc-400">
          <span className="font-mono text-zinc-300">{entry.tool_name}</span>{" "}
          <pre className="mt-1 whitespace-pre-wrap font-mono text-zinc-500">
            {JSON.stringify(entry.arguments)}
          </pre>
        </div>
      );
    case "policy_decided":
      return (
        <div className="flex flex-col gap-1 rounded border border-zinc-800 px-3 py-2 text-xs text-zinc-400">
          <div className="flex items-center gap-2">
            <span className="font-mono text-zinc-300">{entry.tool_name}</span>
            <DecisionBadge action={entry.action} />
          </div>
          <span>{entry.reason}</span>
          {entry.matched_rule_ids.length > 0 && (
            <span className="font-mono text-zinc-500">rules: {entry.matched_rule_ids.join(", ")}</span>
          )}
        </div>
      );
    case "execution_result":
      return (
        <div
          className={`rounded border px-3 py-2 text-xs ${
            entry.ok ? "border-green-500/30 text-green-500" : "border-red-500/30 text-red-500"
          }`}
        >
          <span className="font-mono">{entry.tool_name}</span> {entry.ok ? "completed" : "failed"}
          {entry.error && <pre className="mt-1 whitespace-pre-wrap font-mono">{entry.error}</pre>}
        </div>
      );
    case "approval": {
      const styles: Record<ApprovalStatus, string> = {
        pending: "border-amber-500/30 text-amber-500",
        granted: "border-green-500/30 text-green-500",
        rejected: "border-red-500/30 text-red-500",
      };
      const labels: Record<ApprovalStatus, string> = {
        pending: "Waiting for approval",
        granted: "Approved",
        rejected: "Rejected",
      };
      return (
        <div className={`rounded border px-3 py-2 text-xs ${styles[entry.status]}`}>
          <div className="flex items-center gap-2">
            <span className="font-mono text-zinc-300">{entry.tool_name}</span>
            <span>{labels[entry.status]}</span>
          </div>
          {entry.reason && <span className="mt-1 block">{entry.reason}</span>}
          {Object.keys(entry.arguments).length > 0 && (
            <pre className="mt-1 whitespace-pre-wrap font-mono text-zinc-500">
              {JSON.stringify(entry.arguments)}
            </pre>
          )}
        </div>
      );
    }
    default:
      return null;
  }
}
