/**
 * WebSocket client for the Tutor backend's `/api/v1/ws` endpoint.
 *
 * Handles:
 *  - Connection lifecycle (connect, reconnect, disconnect)
 *  - Sending `start_turn` / `cancel` / `ping` messages
 *  - Receiving StreamEvents and dispatching to handlers
 *  - Aggregating streaming content into final messages
 *
 * Designed for use by the `useWebSocket` hook and the Zustand store.
 */

import type {
  StreamEvent,
  WSClientMessage,
  WSServerMessage,
} from "./types";

export type StreamEventHandler = (event: StreamEvent | WSServerMessage) => void;

export interface WsClientOptions {
  url: string;
  onEvent: StreamEventHandler;
  onOpen?: () => void;
  onClose?: (code: number, reason: string) => void;
  onError?: (err: Event | Error) => void;
  autoReconnect?: boolean;
  reconnectDelayMs?: number;
}

export class WsClient {
  private ws: WebSocket | null = null;
  private opts: Required<WsClientOptions>;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByUser = false;
  private seq = 0;

  constructor(opts: WsClientOptions) {
    this.opts = {
      autoReconnect: true,
      reconnectDelayMs: 1500,
      onOpen: () => undefined,
      onClose: () => undefined,
      onError: () => undefined,
      ...opts,
    };
  }

  connect(): void {
    if (typeof window === "undefined") return; // SSR guard
    this.closedByUser = false;
    try {
      this.ws = new WebSocket(this.opts.url);
    } catch (e) {
      this.opts.onError(e as Error);
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => this.opts.onOpen();
    this.ws.onmessage = (e) => this.handleMessage(e.data as string);
    this.ws.onerror = (e) => {
      this.opts.onError(e);
      this.scheduleReconnect();
    };
    this.ws.onclose = (e) => {
      this.opts.onClose(e.code, e.reason);
      if (!this.closedByUser && this.opts.autoReconnect) {
        this.scheduleReconnect();
      }
    };
  }

  send(msg: WSClientMessage): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  close(code = 1000, reason = "client-close"): void {
    this.closedByUser = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close(code, reason);
      } catch {
        // ignore
      }
      this.ws = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.closedByUser || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, this.opts.reconnectDelayMs);
  }

  private handleMessage(raw: string): void {
    let parsed: WSServerMessage;
    try {
      parsed = JSON.parse(raw) as WSServerMessage;
    } catch (e) {
      console.error("[ws] failed to parse message", e, raw);
      return;
    }

    // Skip protocol-level messages
    if (parsed.type === "pong" || parsed.type === "ack") return;
    if (parsed.type === "job_submitted") {
      this.opts.onEvent?.(parsed);
      return;
    }

    const event: StreamEvent = {
      type: parsed.type,
      source: parsed.source || "",
      stage: parsed.stage || "",
      content: parsed.content || "",
      metadata: parsed.metadata || {},
      session_id: parsed.session_id || "",
      turn_id: parsed.turn_id || "",
      seq: parsed.seq ?? ++this.seq,
      timestamp: parsed.timestamp || Date.now(),
      event_id: parsed.event_id || "",
    };
    this.opts.onEvent(event);
  }
}

/**
 * Helper: build a `start_turn` message.
 */
export const startTurnMessage = (params: {
  message: string;
  userId?: string;
  capability?: string;
  sessionId?: string;
  language?: string;
}): WSClientMessage => ({
  type: "start_turn",
  message: params.message,
  user_id: params.userId,
  capability: params.capability,
  session_id: params.sessionId,
  language: params.language,
});

/**
 * Helper: build a `submit_job` message (Phase 5.2 async flow).
 */
export const startJobMessage = (params: {
  message: string;
  userId?: string;
  capability?: string;
  sessionId?: string;
  language?: string;
  metadata?: Record<string, unknown>;
}): WSClientMessage => ({
  type: "submit_job",
  message: params.message,
  user_id: params.userId,
  capability: params.capability,
  session_id: params.sessionId,
  language: params.language,
  metadata: params.metadata,
});
