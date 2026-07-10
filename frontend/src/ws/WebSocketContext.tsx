import { createContext, useCallback, useContext, useRef, type ReactNode } from "react";
import { useWebSocket, type WsStatus } from "./useWebSocket";
import type { WsEvent } from "../api/types";

type EventListener = (event: WsEvent) => void;
type ReconnectListener = () => void;

interface WebSocketContextValue {
  status: WsStatus;
  subscribe: (fn: EventListener) => () => void;
  onReconnect: (fn: ReconnectListener) => () => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const listenersRef = useRef(new Set<EventListener>());
  const reconnectListenersRef = useRef(new Set<ReconnectListener>());

  const handleMessage = useCallback((e: MessageEvent) => {
    let parsed: WsEvent;
    try {
      parsed = JSON.parse(e.data);
    } catch {
      return;
    }
    for (const fn of listenersRef.current) fn(parsed);
  }, []);

  const handleReconnect = useCallback(() => {
    for (const fn of reconnectListenersRef.current) fn();
  }, []);

  const status = useWebSocket({ onMessage: handleMessage, onReconnect: handleReconnect });

  const subscribe = useCallback((fn: EventListener) => {
    listenersRef.current.add(fn);
    return () => listenersRef.current.delete(fn);
  }, []);

  const onReconnect = useCallback((fn: ReconnectListener) => {
    reconnectListenersRef.current.add(fn);
    return () => reconnectListenersRef.current.delete(fn);
  }, []);

  return (
    <WebSocketContext.Provider value={{ status, subscribe, onReconnect }}>{children}</WebSocketContext.Provider>
  );
}

export function useWebSocketContext(): WebSocketContextValue {
  const ctx = useContext(WebSocketContext);
  if (!ctx) throw new Error("useWebSocketContext must be used within WebSocketProvider");
  return ctx;
}
