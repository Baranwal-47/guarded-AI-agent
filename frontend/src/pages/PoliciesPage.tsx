import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Action, PolicyRule, Tool } from "../api/types";
import { DecisionBadge } from "../components/DecisionBadge";

const RULE_TYPES = ["block_tool", "require_approval", "input_validation", "token_budget"] as const;
type RuleType = (typeof RULE_TYPES)[number];

const ACTION_BY_RULE_TYPE: Record<RuleType, Action> = {
  block_tool: "DENY",
  require_approval: "REQUIRE_APPROVAL",
  input_validation: "DENY",
  token_budget: "REQUIRE_APPROVAL",
};

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
  const [tools, setTools] = useState<Tool[]>([]);

  const [toolName, setToolName] = useState("");
  const [ruleType, setRuleType] = useState<RuleType>("block_tool");
  const [prefix, setPrefix] = useState("");
  const [arg, setArg] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<PolicyRule[]>("/policies/rules")
      .then(setRules)
      .finally(() => setLoaded(true));
    api.get<Tool[]>("/tools").then((fetched) => {
      setTools(fetched);
      setToolName((prev) => prev || fetched[0]?.name || "");
    });
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

  async function submitRule(e: FormEvent) {
    e.preventDefault();
    setFormError(null);

    const condition: Record<string, unknown> = {};
    if (ruleType === "input_validation") {
      condition.prefix = prefix;
      if (arg.trim()) condition.arg = arg;
    } else if (ruleType === "token_budget") {
      condition.max_tokens = Number(maxTokens);
    }

    const body = {
      rule_type: ruleType,
      tool_name: toolName,
      condition,
      action: ACTION_BY_RULE_TYPE[ruleType],
      enabled: true,
    };

    try {
      const { id } = await api.post<{ id: string }>("/policies/rules", body);
      setRules((prev) => [...prev, { id, policy_id: null, ...body }]);
      setPrefix("");
      setArg("");
      setMaxTokens("");
    } catch {
      setFormError("Check the rule fields and try again.");
    }
  }

  const grouped = groupByTool(rules);
  const toolNames = Object.keys(grouped).sort();

  return (
    <div>
      <h1 className="text-xl font-semibold text-zinc-50">Policies</h1>

      <form
        onSubmit={submitRule}
        className="mt-6 flex flex-wrap items-end gap-4 rounded border border-zinc-800 bg-zinc-900 p-4"
      >
        <div className="flex flex-col gap-1">
          <label className="text-xs font-semibold text-zinc-400">Tool</label>
          <select
            value={toolName}
            onChange={(e) => setToolName(e.target.value)}
            className="rounded border border-zinc-800 bg-zinc-950 px-2 py-1 text-sm text-zinc-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {tools.map((tool) => (
              <option key={tool.name} value={tool.name}>
                {tool.name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-semibold text-zinc-400">Rule type</label>
          <select
            value={ruleType}
            onChange={(e) => setRuleType(e.target.value as RuleType)}
            className="rounded border border-zinc-800 bg-zinc-950 px-2 py-1 text-sm text-zinc-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {RULE_TYPES.map((rt) => (
              <option key={rt} value={rt}>
                {rt}
              </option>
            ))}
          </select>
        </div>

        {ruleType === "input_validation" && (
          <>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-semibold text-zinc-400">Prefix</label>
              <input
                value={prefix}
                onChange={(e) => setPrefix(e.target.value)}
                required
                className="rounded border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-sm text-zinc-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-semibold text-zinc-400">Arg (default "path")</label>
              <input
                value={arg}
                onChange={(e) => setArg(e.target.value)}
                placeholder="path"
                className="rounded border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-sm text-zinc-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </>
        )}

        {ruleType === "token_budget" && (
          <div className="flex flex-col gap-1">
            <label className="text-xs font-semibold text-zinc-400">Max tokens</label>
            <input
              type="number"
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
              required
              className="rounded border border-zinc-800 bg-zinc-950 px-2 py-1 text-sm text-zinc-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        )}

        <button
          type="submit"
          disabled={!toolName}
          className="rounded bg-blue-500 px-3 py-1.5 text-sm font-semibold text-white hover:bg-blue-400 disabled:opacity-50"
        >
          Create Rule
        </button>

        {formError && <p className="w-full text-sm text-red-500">{formError}</p>}
      </form>

      {loaded && rules.length === 0 ? (
        <div className="mt-8 text-center">
          <h2 className="text-base font-semibold text-zinc-50">No rules yet</h2>
          <p className="mt-1 text-sm text-zinc-400">Create a rule to start governing tool calls.</p>
        </div>
      ) : (
        <div className="mt-6 space-y-6">
          {toolNames.map((groupName) => (
            <section key={groupName} className="rounded border border-zinc-800 bg-zinc-900">
              <h2 className="border-b border-zinc-800 px-4 py-2 font-mono text-sm font-semibold text-zinc-50">
                {groupName}
              </h2>
              <ul className="divide-y divide-zinc-800">
                {grouped[groupName].map((rule) => (
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
