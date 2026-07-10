import type { Action } from "../api/types";

const STYLES: Record<Action, string> = {
  ALLOW: "bg-green-500/15 text-green-500",
  REQUIRE_APPROVAL: "bg-amber-500/15 text-amber-500",
  DENY: "bg-red-500/15 text-red-500",
};

export function DecisionBadge({ action }: { action: Action }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STYLES[action]}`}>
      {action}
    </span>
  );
}
