import { useEffect, useRef, useState } from "react";

export type WsStatus = "connected" | "reconnecting" | "disconnected";

interface UseWebSocketOptions {
  onMessage: (event: MessageEvent) => void;
  onReconnect?: () => void;
}

/** Hand-rolled reconnecting WebSocket hook — capped exponential backoff, no dependency (RESEARCH Pattern). */
export function useWebSocket({ onMessage, onReconnect }: UseWebSocketOptions): WsStatus {
  const [status, setStatus] = useState<WsStatus>("disconnected");
  const onMessageRef = useRef(onMessage);
  const onReconnectRef = useRef(onReconnect);
  onMessageRef.current = onMessage;
  onReconnectRef.current = onReconnect;

  useEffect(() => {
    // Relative URL through the Vite dev proxy (`/ws`, ws:true) — never hardcode host:port.
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    let socket: WebSocket;
    let attempt = 0;
    let hasConnectedBefore = false;
    let closedByCleanup = false;
    let timer: ReturnType<typeof setTimeout>;

    function connect() {
      socket = new WebSocket(url);
      socket.onopen = () => {
        attempt = 0;
        setStatus("connected");
        if (hasConnectedBefore) onReconnectRef.current?.();
        hasConnectedBefore = true;
      };
      socket.onmessage = (e) => onMessageRef.current(e);
      socket.onclose = () => {
        if (closedByCleanup) return;
        setStatus("reconnecting");
        const delay = Math.min(1000 * 2 ** attempt++, 10000);
        timer = setTimeout(connect, delay);
      };
    }

    connect();
    return () => {
      closedByCleanup = true;
      clearTimeout(timer);
      socket.close();
    };
  }, []);

  return status;
}
