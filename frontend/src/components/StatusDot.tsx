import { useWebSocketContext } from "../ws/WebSocketContext";

const STATUS_COLOR: Record<string, string> = {
  connected: "bg-green-500",
  reconnecting: "bg-amber-500",
  disconnected: "bg-red-500",
};

const STATUS_LABEL: Record<string, string> = {
  connected: "Connected",
  reconnecting: "Reconnecting",
  disconnected: "Disconnected",
};

export function StatusDot() {
  const { status } = useWebSocketContext();
  return (
    <div className="flex items-center gap-2 text-xs text-zinc-400">
      <span className={`h-2 w-2 rounded-full ${STATUS_COLOR[status]}`} />
      <span>{STATUS_LABEL[status]}</span>
    </div>
  );
}
