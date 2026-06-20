/**
 * Per-job event reducer — pure, testable, replayable.
 *
 * The single source of truth for what the user sees when a job runs.
 * Replaces the old "one global activeTurn" model that caused the
 * "task completed but no output" regression: an async job's events
 * could land in the bus, but the reducer ignored them because the
 * activeTurn was already idle, and the synthetic completion heuristic
 * picked ``currentCapability`` after it had been cleared.
 *
 * The reducer:
 *   - keys all state by ``job_id`` (so multiple jobs run in parallel);
 *   - dedupes events by ``event_id`` and orders by ``seq``;
 *   - emits exactly one visible assistant message per terminal job,
 *     using the server-supplied ``assistant_message`` from the
 *     ``JobResultContract`` (no client-side guessing);
 *   - never reads any "current capability" global.
 */

import type {
  ChatMessage,
  JobResultContract,
  JobStatus,
  StreamEvent,
} from "./types";

function toMillis(value: string | number | null | undefined): number | null {
  if (value == null) return null;
  if (typeof value === "number") return value;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** Maximum number of raw events we keep per job (matches backend cap). */
export const MAX_EVENTS_PER_JOB = 200;

/** A job as the frontend sees it. */
export interface ClientJob {
  job_id: string;
  capability: string;
  status: JobStatus;
  message_preview: string;
  submitted_at: number;
  started_at: number | null;
  finished_at: number | null;
  last_seq: number;
  events: StreamEvent[];
  result: JobResultContract | null;
  error: string | null;
  event_count: number;
  /** event_ids we've already applied (for dedup). */
  seen_event_ids: Set<string>;
}

export interface JobsState {
  jobsById: Record<string, ClientJob>;
  jobOrder: string[];
  messages: ChatMessage[];
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

export interface SubmitEvent {
  type: "submit";
  job_id: string;
  capability: string;
  message_preview?: string;
  submitted_at?: number;
}

export interface StreamReducerEvent {
  type: "stream";
  event: StreamEvent;
  /** The job_id the dispatcher resolved from event metadata. */
  job_id: string;
}

export interface TerminalReducerEvent {
  type: "job_terminal";
  job_id: string;
  capability: string;
  result: JobResultContract;
  timestamp?: number;
  event_id?: string;
}

export interface SnapshotReducerEvent {
  type: "snapshot";
  /** A previously persisted job (re-hydrated from REST after page reload). */
  job: {
    job_id: string;
    capability: string;
    status: JobStatus;
    message_preview?: string;
    submitted_at?: number;
    started_at?: string | number | null;
    finished_at?: string | number | null;
    last_seq?: number;
    events?: StreamEvent[];
    result?: JobResultContract | null;
    error?: string | null;
    event_count?: number;
  };
}

export type ReducerEvent =
  | SubmitEvent
  | StreamReducerEvent
  | TerminalReducerEvent
  | SnapshotReducerEvent;

// ---------------------------------------------------------------------------
// State factory
// ---------------------------------------------------------------------------

export function createJobState(
  job_id: string,
  capability: string,
  initialMessages: ChatMessage[] = [],
  message_preview: string = "",
  submitted_at: number = Date.now(),
): JobsState {
  const job: ClientJob = {
    job_id,
    capability,
    status: "pending",
    message_preview,
    submitted_at,
    started_at: null,
    finished_at: null,
    last_seq: 0,
    events: [],
    result: null,
    error: null,
    event_count: 0,
    seen_event_ids: new Set(),
  };
  return {
    jobsById: { [job_id]: job },
    jobOrder: [job_id],
    messages: [...initialMessages],
  };
}

export function emptyJobsState(messages: ChatMessage[] = []): JobsState {
  return {
    jobsById: {},
    jobOrder: [],
    messages: [...messages],
  };
}

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

export function reduceJobEvent(state: JobsState, ev: ReducerEvent): JobsState {
  switch (ev.type) {
    case "submit":
      return applySubmit(state, ev);
    case "stream":
      return applyStream(state, ev);
    case "job_terminal":
      return applyTerminal(state, ev);
    case "snapshot":
      return applySnapshot(state, ev);
    default: {
      // Exhaustive: if a new variant is added, the compiler will catch it.
      const _exhaustive: never = ev;
      void _exhaustive;
      return state;
    }
  }
}

function applySubmit(state: JobsState, ev: SubmitEvent): JobsState {
  if (state.jobsById[ev.job_id]) {
    // Already known (re-submit of same job, or duplicate ack) — leave as is.
    return state;
  }
  const job: ClientJob = {
    job_id: ev.job_id,
    capability: ev.capability,
    status: "pending",
    message_preview: ev.message_preview ?? "",
    submitted_at: ev.submitted_at ?? Date.now(),
    started_at: null,
    finished_at: null,
    last_seq: 0,
    events: [],
    result: null,
    error: null,
    event_count: 0,
    seen_event_ids: new Set(),
  };
  return {
    jobsById: { ...state.jobsById, [ev.job_id]: job },
    jobOrder: [ev.job_id, ...state.jobOrder.filter((j) => j !== ev.job_id)],
    messages: state.messages,
  };
}

function applyStream(state: JobsState, ev: StreamReducerEvent): JobsState {
  const job = state.jobsById[ev.job_id];
  if (!job) {
    // Drop silently: the reducer is strict about job ownership. The
    // dispatcher is responsible for inserting the job via ``submit``
    // before any events arrive.
    return state;
  }
  const stream = ev.event;

  // Dedup by event_id.
  if (stream.event_id && job.seen_event_ids.has(stream.event_id)) {
    return state;
  }

  // Order: ignore out-of-order duplicates unless seq is newer.
  if (typeof stream.seq === "number" && stream.seq <= job.last_seq && stream.event_id && job.seen_event_ids.has(stream.event_id) === false) {
    // First time seeing this event_id but seq is older than last_seq — still
    // take it (different ids may have collided seqs in rare cases). Only
    // truly duplicate event_ids are dropped above.
  }

  const nextEvents = [...job.events, stream];
  const trimmed =
    nextEvents.length > MAX_EVENTS_PER_JOB
      ? nextEvents.slice(nextEvents.length - MAX_EVENTS_PER_JOB)
      : nextEvents;
  const seen = new Set(job.seen_event_ids);
  if (stream.event_id) seen.add(stream.event_id);

  // First stage_start marks the job as running.
  let status: JobStatus = job.status;
  let started_at = job.started_at;
  if (stream.type === "stage_start" && job.status === "pending") {
    status = "running";
    started_at = stream.timestamp ? stream.timestamp * 1000 : Date.now();
  }

  const next: ClientJob = {
    ...job,
    status,
    started_at,
    events: trimmed,
    event_count: job.event_count + 1,
    last_seq:
      typeof stream.seq === "number" && stream.seq > job.last_seq
        ? stream.seq
        : job.last_seq,
    seen_event_ids: seen,
  };
  return {
    ...state,
    jobsById: { ...state.jobsById, [ev.job_id]: next },
  };
}

function applyTerminal(state: JobsState, ev: TerminalReducerEvent): JobsState {
  const job = state.jobsById[ev.job_id];
  // Map contract status onto the persistent JobStatus enum.
  const status: JobStatus = ev.result.status;
  const finishedAt =
    ev.timestamp ? ev.timestamp * 1000 : Date.now();

  let messages = state.messages;
  // Avoid double-appending if a previous terminal already produced a message
  // for this job (e.g. snapshot replay + live event).
  const alreadyMessaged = state.messages.some(
    (m) => m.metadata?.job_id === ev.job_id && m.metadata?.terminal === true,
  );
  if (!alreadyMessaged && ev.result.assistant_message) {
    const assistantMsg: ChatMessage = {
      id: `job_terminal_${ev.job_id}_${ev.timestamp ?? Date.now()}`,
      role: "assistant",
      agent: ev.capability,
      content: ev.result.assistant_message,
      timestamp: finishedAt,
      metadata: {
        job_id: ev.job_id,
        terminal: true,
        contract: ev.result,
        ...(ev.event_id ? { event_id: ev.event_id } : {}),
      },
    };
    messages = [...state.messages, assistantMsg];
  }

  if (!job) {
    // Terminal arrived before submit (e.g. replay from snapshot). Insert a
    // minimal job and the assistant message.
    const fresh: ClientJob = {
      job_id: ev.job_id,
      capability: ev.capability,
      status,
      message_preview: "",
      submitted_at: finishedAt,
      started_at: finishedAt,
      finished_at: finishedAt,
      last_seq: ev.result.event_cursor ?? 0,
      events: [],
      result: ev.result,
      error: ev.result.error?.message ?? null,
      event_count: 0,
      seen_event_ids: new Set(ev.event_id ? [ev.event_id] : []),
    };
    return {
      jobsById: { ...state.jobsById, [ev.job_id]: fresh },
      jobOrder: state.jobOrder.includes(ev.job_id)
        ? state.jobOrder
        : [ev.job_id, ...state.jobOrder],
      messages,
    };
  }

  // The terminal event itself goes into the events buffer so a replay
  // yields the same final state.
  const seen = new Set(job.seen_event_ids);
  if (ev.event_id) seen.add(ev.event_id);
  const terminalStream: StreamEvent = {
    type: "job_terminal",
    source: "job_runner",
    stage: "terminal",
    content: ev.result.assistant_message,
    metadata: { job_id: ev.job_id, contract: ev.result },
    session_id: "",
    turn_id: "",
    seq: ev.result.event_cursor ?? job.last_seq + 1,
    timestamp: (ev.timestamp ?? Date.now() / 1000),
    event_id: ev.event_id ?? `terminal_${ev.job_id}_${finishedAt}`,
  };
  const nextEvents = [...job.events, terminalStream];
  const trimmed =
    nextEvents.length > MAX_EVENTS_PER_JOB
      ? nextEvents.slice(nextEvents.length - MAX_EVENTS_PER_JOB)
      : nextEvents;

  const next: ClientJob = {
    ...job,
    status,
    finished_at: finishedAt,
    last_seq:
      ev.result.event_cursor && ev.result.event_cursor > job.last_seq
        ? ev.result.event_cursor
        : job.last_seq,
    events: trimmed,
    result: ev.result,
    error: ev.result.error?.message ?? null,
    seen_event_ids: seen,
  };
  return {
    ...state,
    jobsById: { ...state.jobsById, [ev.job_id]: next },
    messages,
  };
}

function applySnapshot(state: JobsState, ev: SnapshotReducerEvent): JobsState {
  const incoming = ev.job;
  const existing = state.jobsById[incoming.job_id];

  // If we have a fresher local view (newer last_seq or more events), keep it.
  if (existing) {
    const localNewer =
      (toMillis(incoming.finished_at) ?? 0) < (existing.finished_at ?? 0) ||
      (existing.last_seq ?? 0) > (incoming.last_seq ?? 0);
    if (localNewer) {
      return state;
    }
  }

  const seen = new Set<string>(
    (incoming.events ?? [])
      .map((e) => e.event_id)
      .filter((id): id is string => typeof id === "string" && id.length > 0),
  );
  const next: ClientJob = {
    job_id: incoming.job_id,
    capability: incoming.capability,
    status: incoming.status,
    message_preview: incoming.message_preview ?? "",
    submitted_at: incoming.submitted_at ?? Date.now(),
    started_at: toMillis(incoming.started_at),
    finished_at: toMillis(incoming.finished_at),
    last_seq: incoming.last_seq ?? 0,
    events: incoming.events ?? [],
    result: incoming.result ?? null,
    error: incoming.error ?? null,
    event_count: incoming.event_count ?? (incoming.events?.length ?? 0),
    seen_event_ids: seen,
  };
  const jobOrder = state.jobOrder.includes(incoming.job_id)
    ? state.jobOrder
    : [incoming.job_id, ...state.jobOrder];

  // If the snapshot is already terminal, surface the assistant message
  // (replay-safe: skip if we already have one for this job).
  let messages = state.messages;
  if (next.result && isTerminal(next.status)) {
    const already = messages.some(
      (m) => m.metadata?.job_id === incoming.job_id && m.metadata?.terminal === true,
    );
    if (!already) {
      messages = [
        ...messages,
        {
          id: `snapshot_terminal_${incoming.job_id}`,
          role: "assistant",
          agent: incoming.capability,
          content: next.result.assistant_message,
          timestamp: next.finished_at ?? Date.now(),
          metadata: {
            job_id: incoming.job_id,
            terminal: true,
            contract: next.result,
            replay: true,
          },
        },
      ];
    }
  }

  return {
    ...state,
    jobsById: { ...state.jobsById, [incoming.job_id]: next },
    jobOrder,
    messages,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function isTerminal(status: JobStatus): boolean {
  return (
    status === "succeeded" ||
    status === "partial" ||
    status === "failed" ||
    status === "cancelled"
  );
}

export function getJobIdFromEvent(
  event: { metadata?: Record<string, unknown> | null },
): string | null {
  const md = event.metadata as Record<string, unknown> | undefined;
  if (md && typeof md.job_id === "string" && md.job_id.length > 0) {
    return md.job_id;
  }
  return null;
}

// Test helpers (used by job-reducer.test.ts and event-handler tests).
export const __test__ = {
  applySubmit,
  applyStream,
  applyTerminal,
  applySnapshot,
};
