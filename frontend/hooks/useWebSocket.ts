/**
 * useWebSocket — connect to backend's /api/v1/ws endpoint, expose state.
 *
 * Wires:
 *  - WsClient (lib/ws.ts)
 *  - dispatchStreamEvent (lib/event-handler.ts)
 *  - Zustand store
 */

"use client";

import { useEffect, useRef } from "react";
import { resolveWebSocketUrl, WsClient, startTurnMessage } from "@/lib/ws";
import { useTutorStore } from "@/lib/store";
import { dispatchStreamEvent } from "@/lib/event-handler";

export interface UseWebSocketOptions {
  /** Override WS URL; defaults to the direct backend in development. */
  url?: string;
  /** Auto-connect on mount (default true) */
  autoConnect?: boolean;
}

export function useWebSocket(opts: UseWebSocketOptions = {}): void {
  const { url, autoConnect = true } = opts;
  const setConnected = useTutorStore((s) => s.setWsConnected);
  const clientRef = useRef<WsClient | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!autoConnect) return;

    const resolved = resolveWebSocketUrl(url);

    let disposed = false;
    const client = new WsClient({
      url: resolved,
      onOpen: () => setConnected(true),
      onClose: () => setConnected(false),
      onError: () => setConnected(false),
      onEvent: dispatchStreamEvent,
    });
    const connectTimer = window.setTimeout(() => {
      if (disposed) return;
      clientRef.current = client;
      client.connect();
    }, 0);

    return () => {
      disposed = true;
      window.clearTimeout(connectTimer);
      if (clientRef.current === client) {
        client.close();
        clientRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/**
 * Send a new user message via the WebSocket.
 * Adds the user message to the chat history and starts an active turn.
 */
export function sendUserMessage(text: string): boolean {
  const store = useTutorStore.getState();
  if (!text.trim()) return false;

  // Append user message
  store.addMessage({ role: "user", content: text });
  // Generate a turn id
  const turnId =
    typeof crypto !== "undefined" && (crypto as any).randomUUID
      ? (crypto as any).randomUUID()
      : `t_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  store.startActiveTurn(turnId, store.currentCapability || "resource_generation");

  // Connect (or reconnect) and send
  const url = resolveWebSocketUrl();
  const client = new WsClient({
    url,
    onOpen: () => {
      store.setWsConnected(true);
      client.send(
        startTurnMessage({
          message: text,
          userId: store.userId,
          capability: store.currentCapability || undefined,
          sessionId: store.sessionId,
          language: store.language,
        }),
      );
    },
    onClose: () => store.setWsConnected(false),
    onError: () => store.setWsConnected(false),
    onEvent: dispatchStreamEvent,
  });
  client.connect();

  // Auto-close after a long idle (the server closes after the turn).
  // We rely on the server's `done` event triggering `completeActiveTurn`.
  setTimeout(() => client.close(), 600_000); // 10 min safety
  return true;
}

/**
 * Cancel the currently active turn (best-effort).
 *
 * Backend protocol: send `{ type: "cancel", turn_id }`.
 * On the client we also mark the turn as cancelled locally so the UI
 * stops showing the spinner even if the server doesn't reply.
 */
export function cancelActiveTurn(): void {
  const store = useTutorStore.getState();
  const turnId = store.activeTurn.turn_id;
  if (!turnId) return;

  // Locally mark as error so UI stops spinning.
  store.completeActiveTurn(null, "用户已取消");

  // Best-effort server cancel (open a transient connection).
  if (typeof window === "undefined") return;
  try {
    const url = resolveWebSocketUrl();
    const client = new WsClient({
      url,
      onOpen: () => {
        client.send({ type: "cancel", turn_id: turnId });
        // Give the server a moment then close.
        setTimeout(() => client.close(), 500);
      },
      onEvent: () => undefined,
    });
    client.connect();
  } catch {
    // ignore — local cancel already applied
  }
}
