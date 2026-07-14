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
  /** Base delay in ms before the first reconnect attempt. Default 1500. */
  reconnectDelayMs?: number;
  /** Maximum delay in ms after exponential growth. Default 30000 (30s). */
  maxReconnectDelayMs?: number;
  /** Multiplier applied to delay after each failed attempt. Default 2. */
  backoffMultiplier?: number;
  /** Maximum reconnect attempts before giving up. Default 0 (unlimited). */
  maxReconnectAttempts?: number;
  /**
   * Fraction of jitter to apply (0.0-1.0). 0 = none, 0.2 = ±20%.
   * Default 0.2. Jitter avoids thundering-herd reconnect storms
   * when many clients lose connectivity simultaneously.
   */
  jitterFraction?: number;
}

const DEFAULT_RECONNECT_DELAY_MS = 1500;
const DEFAULT_MAX_RECONNECT_DELAY_MS = 30_000;
const DEFAULT_BACKOFF_MULTIPLIER = 2;
const DEFAULT_JITTER_FRACTION = 0.2;

export class WsClient {
  private ws: WebSocket | null = null;
  private opts: Required<WsClientOptions> & {
    maxReconnectDelayMs: number;
    backoffMultiplier: number;
    maxReconnectAttempts: number;
    jitterFraction: number;
  };
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByUser = false;
  private seq = 0;
  private _reconnectAttempt = 0;

  constructor(opts: WsClientOptions) {
    this.opts = {
      autoReconnect: true,
      reconnectDelayMs: DEFAULT_RECONNECT_DELAY_MS,
      maxReconnectDelayMs: DEFAULT_MAX_RECONNECT_DELAY_MS,
      backoffMultiplier: DEFAULT_BACKOFF_MULTIPLIER,
      maxReconnectAttempts: 0,
      jitterFraction: DEFAULT_JITTER_FRACTION,
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
    this.ws.onopen = () => {
      // 2026-06-21 plan: reset the backoff counter on a successful
      // connect so the next disconnection starts from the base
      // delay again — the spec calls for "exponential backoff",
      // not "indefinite growth across the session lifetime".
      this._reconnectAttempt = 0;
      this.opts.onOpen();
    };
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
    const maxAttempts = this.opts.maxReconnectAttempts;
    if (maxAttempts > 0 && this._reconnectAttempt >= maxAttempts) {
      return; // exhausted retries
    }
    this._reconnectAttempt += 1;
    // 2026-06-21 plan (B1): exponential backoff with jitter.
    // Delay = min(base * multiplier^(attempt-1), max_delay) * (1 + jitter * random).
    const attempt = this._reconnectAttempt;
    const base = this.opts.reconnectDelayMs;
    const mult = this.opts.backoffMultiplier;
    const maxDelay = this.opts.maxReconnectDelayMs;
    const raw = Math.min(
      base * Math.pow(mult, attempt - 1),
      maxDelay,
    );
    const jitter = this.opts.jitterFraction;
    const jitterRange = raw * jitter;
    const jittered =
      raw + (Math.random() * 2 - 1) * jitterRange;
    const final = Math.max(100, Math.round(jittered));
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, final);
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
