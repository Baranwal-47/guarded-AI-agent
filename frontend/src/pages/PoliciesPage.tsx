import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { PolicyRule } from "../api/types";
import { DecisionBadge } from "../components/DecisionBadge";

function conditionSummary(rule: PolicyRule): string {
  if (rule.rule_type === "input_validation") {
    const prefix = rule.condition.prefix as string | undefined;
    const arg = (rule.condition.arg as string | undefined) ?? "path";
    return `${arg} must start with "${prefix ?? ""}"`;
  }
  if (rule.rule_type === "token_budget") {
    return `max_tokens=${rule.condition.max_tokens ?? ""}`;
  }
  return "";
}

function groupByTool(rules: PolicyRule[]): Record<string, PolicyRule[]> {
  return rules.reduce<Record<string, PolicyRule[]>>((acc, rule) => {
    (acc[rule.tool_name] ??= []).push(rule);
    return acc;
  }, {});
}

export function PoliciesPage() {
  const [rules, setRules] = useState<PolicyRule[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api
      .get<PolicyRule[]>("/policies/rules")
      .then(setRules)
      .finally(() => setLoaded(true));
  }, []);

  async function toggleRule(rule: PolicyRule) {
    const enabled = !rule.enabled;
    await api.patch(`/policies/rules/${rule.id}`, { enabled });
    setRules((prev) => prev.map((r) => (r.id === rule.id ? { ...r, enabled } : r)));
  }

  async function deleteRule(rule: PolicyRule) {
    if (!window.confirm("Delete this rule? This cannot be undone.")) return;
    await api.delete(`/policies/rules/${rule.id}`);
    setRules((prev) => prev.filter((r) => r.id !== rule.id));
  }

  const grouped = groupByTool(rules);
  const toolNames = Object.keys(grouped).sort();

  return (
    <div>
      <h1 className="text-xl font-semibold text-zinc-50">Policies</h1>

      {loaded && rules.length === 0 ? (
        <div className="mt-8 text-center">
          <h2 className="text-base font-semibold text-zinc-50">No rules yet</h2>
          <p className="mt-1 text-sm text-zinc-400">Create a rule to start governing tool calls.</p>
        </div>
      ) : (
        <div className="mt-6 space-y-6">
          {toolNames.map((toolName) => (
            <section key={toolName} className="rounded border border-zinc-800 bg-zinc-900">
              <h2 className="border-b border-zinc-800 px-4 py-2 font-mono text-sm font-semibold text-zinc-50">
                {toolName}
              </h2>
              <ul className="divide-y divide-zinc-800">
                {grouped[toolName].map((rule) => (
                  <li key={rule.id} className="flex items-center justify-between gap-4 px-4 py-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <DecisionBadge action={rule.action} />
                      <span className="text-sm text-zinc-50">{rule.rule_type}</span>
                      <span className="truncate font-mono text-xs text-zinc-400">{conditionSummary(rule)}</span>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <label className="flex items-center gap-2 text-xs text-zinc-400">
                        <input
                          type="checkbox"
                          checked={rule.enabled}
                          onChange={() => toggleRule(rule)}
                        />
                        Enabled
                      </label>
                      <button
                        onClick={() => deleteRule(rule)}
                        className="text-xs font-semibold text-red-500 hover:text-red-400"
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
