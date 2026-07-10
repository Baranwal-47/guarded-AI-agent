import { useCallback, useEffect, useState } from "react";
import { useWebSocketContext } from "../ws/WebSocketContext";
import { api } from "../api/client";
import type { Action, AuditLog, Tool, ToolExecution } from "../api/types";
import { DecisionBadge } from "../components/DecisionBadge";

type Tab = "executions" | "logs";

// Every AuditLog row's `event` is one of the 8 locked WS event types (03-01) —
// enumerate them for the filter select instead of a free-text input.
const EVENT_TYPES = [
  "tool_requested",
  "policy_decided",
  "approval_required",
  "approval_granted",
  "approval_rejected",
  "execution_started",
  "execution_completed",
  "execution_failed",
];

const DECISIONS: Action[] = ["ALLOW", "DENY", "REQUIRE_APPROVAL"];

const EMPTY_HEADING = "No executions yet";
const EMPTY_BODY = "Tool call history will appear here once the agent runs.";

export function AuditLogsPage() {
  const { subscribe } = useWebSocketContext();
  const [tab, setTab] = useState<Tab>("executions");
  const [tools, setTools] = useState<Tool[]>([]);

  const [executions, setExecutions] = useState<ToolExecution[]>([]);
  const [execToolName, setExecToolName] = useState("");
  const [execDecision, setExecDecision] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [executionsLoaded, setExecutionsLoaded] = useState(false);

  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [logEvent, setLogEvent] = useState("");
  const [logsLoaded, setLogsLoaded] = useState(false);

  const fetchExecutions = useCallback(async (toolName: string, decision: string) => {
    const params = new URLSearchParams();
    if (toolName) params.set("tool_name", toolName);
    if (decision) params.set("decision", decision);
    const qs = params.toString();
    try {
      const rows = await api.get<ToolExecution[]>(`/audit/executions${qs ? `?${qs}` : ""}`);
      setExecutions(rows);
    } catch {
      // Best-effort; leave the existing rows in place on a failed re-fetch.
    } finally {
      setExecutionsLoaded(true);
    }
  }, []);

  const fetchLogs = useCallback(async (event: string) => {
    const params = new URLSearchParams();
    if (event) params.set("event", event);
    const qs = params.toString();
    try {
      const rows = await api.get<AuditLog[]>(`/audit/logs${qs ? `?${qs}` : ""}`);
      setLogs(rows);
    } catch {
      // Best-effort; leave the existing rows in place on a failed re-fetch.
    } finally {
      setLogsLoaded(true);
    }
  }, []);

  useEffect(() => {
    api
      .get<Tool[]>("/tools")
      .then(setTools)
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchExecutions(execToolName, execDecision);
  }, [fetchExecutions, execToolName, execDecision]);

  useEffect(() => {
    fetchLogs(logEvent);
  }, [fetchLogs, logEvent]);

  // Live update (D-11): the WS lifecycle events don't carry the persisted
  // row's id (only tool_name/result_ok — see 03-01's locked schema), so
  // there's no id to prepend-and-dedupe a synthetic row against. Re-fetching
  // the current filtered view on every relevant event both surfaces the new
  // row live (newest-first ordering puts it on top) and is dedupe-proof by
  // construction, since it's a full replace rather than an append.
  useEffect(() => {
    const unsub = subscribe((event) => {
      if (event.type === "execution_completed" || event.type === "execution_failed") {
        fetchExecutions(execToolName, execDecision);
      }
      // Every event type also writes an audit_logs row (backend 03-01).
      fetchLogs(logEvent);
    });
    return unsub;
  }, [subscribe, fetchExecutions, fetchLogs, execToolName, execDecision, logEvent]);

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div>
      <h1 className="text-xl font-semibold text-zinc-50">Audit Logs</h1>

      <div className="mt-4 flex gap-2 border-b border-zinc-800">
        <TabButton active={tab === "executions"} onClick={() => setTab("executions")}>
          Tool Executions
        </TabButton>
        <TabButton active={tab === "logs"} onClick={() => setTab("logs")}>
          Audit Log
        </TabButton>
      </div>

      {tab === "executions" ? (
        <div className="mt-4">
          <div className="flex gap-2">
            <select
              value={execToolName}
              onChange={(e) => setExecToolName(e.target.value)}
              className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-sm text-zinc-50"
            >
              <option value="">All tools</option>
              {tools.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name}
                </option>
              ))}
            </select>
            <select
              value={execDecision}
              onChange={(e) => setExecDecision(e.target.value)}
              className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-sm text-zinc-50"
            >
              <option value="">All decisions</option>
              {DECISIONS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>

          {executionsLoaded && executions.length === 0 ? (
            <div className="mt-8 text-center">
              <h2 className="text-base font-semibold text-zinc-50">{EMPTY_HEADING}</h2>
              <p className="mt-1 text-sm text-zinc-400">{EMPTY_BODY}</p>
            </div>
          ) : (
            <div className="mt-4 flex flex-col gap-2">
              {executions.map((e) => (
                <div key={e.id} className="rounded border border-zinc-800 bg-zinc-900 px-4 py-2">
                  <button
                    onClick={() => toggleExpanded(e.id)}
                    className="flex w-full items-center justify-between gap-2 text-left"
                  >
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono text-zinc-50">{e.tool_name}</span>
                      <DecisionBadge action={e.decision_action} />
                      {e.flagged_prompt_injection && (
                        <span className="rounded bg-red-500/15 px-2 py-0.5 text-xs font-semibold text-red-500">
                          flagged
                        </span>
                      )}
                      <span
                        className={`text-xs ${e.result_ok === false ? "text-red-500" : "text-zinc-400"}`}
                      >
                        {e.result_ok === null ? "pending" : e.result_ok ? "ok" : "error"}
                      </span>
                    </div>
                    <span className="text-xs text-zinc-500">{e.created_at}</span>
                  </button>
                  {expanded.has(e.id) && (
                    <div className="mt-2 flex flex-col gap-2 border-t border-zinc-800 pt-2 text-xs text-zinc-400">
                      <p>{e.decision_reason}</p>
                      {e.matched_rule_ids.length > 0 && (
                        <p className="font-mono text-zinc-500">rules: {e.matched_rule_ids.join(", ")}</p>
                      )}
                      <pre className="whitespace-pre-wrap font-mono text-zinc-500">
                        {JSON.stringify(e.arguments, null, 2)}
                      </pre>
                      {e.result_error && (
                        <pre className="whitespace-pre-wrap font-mono text-red-500">{e.result_error}</pre>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="mt-4">
          <select
            value={logEvent}
            onChange={(e) => setLogEvent(e.target.value)}
            className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-sm text-zinc-50"
          >
            <option value="">All events</option>
            {EVENT_TYPES.map((ev) => (
              <option key={ev} value={ev}>
                {ev}
              </option>
            ))}
          </select>

          {logsLoaded && logs.length === 0 ? (
            <div className="mt-8 text-center">
              <h2 className="text-base font-semibold text-zinc-50">{EMPTY_HEADING}</h2>
              <p className="mt-1 text-sm text-zinc-400">{EMPTY_BODY}</p>
            </div>
          ) : (
            <div className="mt-4 flex flex-col gap-2">
              {logs.map((l) => (
                <div key={l.id} className="rounded border border-zinc-800 bg-zinc-900 px-4 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-zinc-50">{l.event}</span>
                    <span className="text-zinc-500">{l.created_at}</span>
                  </div>
                  {l.flags && <p className="mt-1 text-amber-500">{l.flags}</p>}
                  <pre className="mt-1 whitespace-pre-wrap font-mono text-zinc-500">
                    {JSON.stringify(l.detail, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-2 text-sm font-semibold ${
        active ? "border-b-2 border-blue-500 text-zinc-50" : "text-zinc-400"
      }`}
    >
      {children}
    </button>
  );
}
