import { NavLink } from "react-router";
import { MessageSquare, ShieldCheck, CircleCheck, ScrollText } from "lucide-react";
import { StatusDot } from "./StatusDot";

const NAV_ITEMS = [
  { to: "/", label: "Agent", icon: MessageSquare },
  { to: "/policies", label: "Policies", icon: ShieldCheck },
  { to: "/approvals", label: "Approvals", icon: CircleCheck },
  { to: "/audit", label: "Audit Logs", icon: ScrollText },
];

export function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col justify-between bg-zinc-900 p-6">
      <nav className="flex flex-col gap-1">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-2 rounded px-3 py-2 text-sm ${
                isActive ? "bg-blue-500/15 text-blue-500" : "text-zinc-400 hover:text-zinc-50"
              }`
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>
      <StatusDot />
    </aside>
  );
}
