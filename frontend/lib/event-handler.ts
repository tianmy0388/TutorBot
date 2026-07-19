/**
 * StreamEvent → Store dispatch logic.
 *
 * Every event MUST carry a ``job_id`` in its metadata. The dispatcher
 * routes events to the typed job reducer (lib/job-reducer.ts), which
 * keeps per-job state. We never infer event ownership from a global
 * ``currentCapability`` — that heuristic caused the no-output regression.
 *
 * Result payloads inside ``result`` events still flow through
 * ``routeResult`` for backwards compatibility with capability-shaped
 * consumers (resource packages, tutoring answers, etc.).
 */

import { useTutorStore } from "./store";
import { getJobIdFromEvent } from "./job-reducer";
import type { ClientJob } from "./job-reducer";
import { workflowMessage } from "./workflow-snapshot";
import { getResourcePackageDetail } from "./api";
import {
  isUsableResourcePackage,
  isUsableStreamedResource,
} from "./resource-validation";
import type {
  ConversationMessageInput,
  StreamEvent,
  StructuredError,
} from "./types";
import {
  type AssessmentReport,
  type PlannedPath,
  type ResourcePackage,
  type StrategyDecision,
  type TutoringAnswer,
  type EnrichmentSuggestion,
  type QuestionUnderstanding,
} from "./types";

export interface StreamDispatchContext {
  sessionId?: string;
  userId?: string;
  appendConversationMessage?: ConversationMessageAppender;
}

export type ConversationMessageAppender = (
  userId: string,
  sessionId: string,
  message: ConversationMessageInput,
) => Promise<unknown>;

const STREAM_EVENT_TYPES = new Set<StreamEvent["type"]>([
  "stage_start",
  "stage_end",
  "thinking",
  "observation",
  "content",
  "content_final",
  "tool_call",
  "tool_result",
  "progress",
  "sources",
  "resource",
  "result",
  "error",
  "cancelled",
  "session",
  "done",
  "job_terminal",
]);

interface InactiveStageHistory {
  events: StreamEvent[];
  openStages: string[];
}

interface InactiveTerminalPersistence {
  userId: string;
  sessionId: string;
  assistant: ConversationMessageInput | null;
  workflow: ConversationMessageInput;
  assistantDone: boolean;
  workflowDone: boolean;
  assistantInFlight: boolean;
  workflowInFlight: boolean;
  finalizedAt: number | null;
}

const inactiveStageHistory = new Map<string, InactiveStageHistory>();
const inactiveTerminalPersistence = new Map<string, InactiveTerminalPersistence>();
const MAX_INACTIVE_STAGE_HISTORIES = 256;
const MAX_INACTIVE_TERMINAL_RECORDS = 256;
const resourceRecoveryInFlight = new Map<string, Promise<void>>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStreamEventType(value: unknown): value is StreamEvent["type"] {
  return (
    typeof value === "string" &&
    STREAM_EVENT_TYPES.has(value as StreamEvent["type"])
  );
}

/** Narrow an untrusted WebSocket payload at one explicit public boundary. */
export function parseStreamEvent(value: unknown): StreamEvent | null {
  if (!isRecord(value) || !isStreamEventType(value.type)) return null;
  const metadata = isRecord(value.metadata) ? value.metadata : {};
  return {
    type: value.type,
    source: typeof value.source === "string" ? value.source : "",
    stage: typeof value.stage === "string" ? value.stage : "",
    content: typeof value.content === "string" ? value.content : "",
    metadata,
    session_id: typeof value.session_id === "string" ? value.session_id : "",
    turn_id: typeof value.turn_id === "string" ? value.turn_id : "",
    seq: typeof value.seq === "number" && Number.isFinite(value.seq) ? value.seq : 0,
    timestamp:
      typeof value.timestamp === "number" && Number.isFinite(value.timestamp)
        ? value.timestamp
        : Date.now() / 1000,
    event_id: typeof value.event_id === "string" ? value.event_id : "",
  };
}

/**
 * Compatibility adapter: capabilities that were designed for the
 * single-activeTurn model still emit ``result`` / ``error`` / ``done``
 * / ``cancelled`` events on a per-job basis. We split the dispatch into
 * the job reducer (for ownership and replay) and the result router
 * (for capability-specific payload dispatch).
 */
export function dispatchStreamEvent(
  ev: unknown,
  context: StreamDispatchContext = {},
): void {
  // Protocol / ack messages (job_submitted, ack, pong) are handled by the
  // WsClient itself; we shouldn't see them here. Defensive no-op.
  if (
    isRecord(ev) &&
    (ev.type === "ack" || ev.type === "pong" || ev.type === "job_submitted")
  ) {
    return;
  }
  const streamEv = parseStreamEvent(ev);
  if (!streamEv) return;
  const metadataSessionId =
    typeof streamEv.metadata?.session_id === "string"
      ? streamEv.metadata.session_id
      : "";
  const authoritativeSessionId =
    context.sessionId || streamEv.session_id || metadataSessionId;
  // A stream event without any authoritative conversation identity cannot
  // safely be projected or persisted. Failing closed prevents a malformed
  // replay/subscription event from being attached to whichever session is
  // currently visible.
  if (!authoritativeSessionId) {
    return;
  }
  const stateAtDispatch = useTutorStore.getState();
  if (
    authoritativeSessionId &&
    stateAtDispatch.sessionId !== authoritativeSessionId
  ) {
    const inactiveJobId = getJobIdFromEvent(streamEv);
    if (inactiveJobId) {
      recordInactiveStage(authoritativeSessionId, inactiveJobId, streamEv);
    }
    if (streamEv.type === "job_terminal" && inactiveJobId) {
      const contract = (
        streamEv.metadata as Record<string, unknown> | undefined
      )?.contract as Record<string, unknown> | undefined;
      const inactiveUserId = context.userId || stateAtDispatch.userId;
      if (
        inactiveUserId &&
        hasTerminalWorkflowStatus(contract)
      ) {
        const persistence = getOrCreateInactiveTerminalPersistence(
          contract,
          inactiveJobId,
          inactiveUserId,
          authoritativeSessionId,
          streamEv,
          takeInactiveStageHistory(authoritativeSessionId, inactiveJobId),
        );
        if (persistence) {
          attemptInactiveTerminalPersistence(
            stageHistoryKey(authoritativeSessionId, inactiveJobId),
            persistence,
            context.appendConversationMessage,
          );
        }
      }
    }
    return;
  }

  const jobId = getJobIdFromEvent(streamEv);
  if (!jobId) {
    stateAtDispatch.addMessage({
      role: "system",
      content: `协议错误：${streamEv.type} 事件缺少 job_id`,
      metadata: { protocol_error: true, event_type: streamEv.type },
    });
    return;
  }

  const hadWorkflowTimeline =
    streamEv.type === "job_terminal" &&
    stateAtDispatch.messages.some((message) => message.id === `workflow:${jobId}`);

  // 1. Always reduce into per-job state.
  useTutorStore.getState().applyStreamEvent(streamEv);

  // 2. Capability-specific routing for known event types.
  switch (streamEv.type) {
    case "result": {
      try {
        const payload: unknown = JSON.parse(streamEv.content);
        if (isRecord(payload)) routeResult(payload, streamEv, context);
      } catch (e) {
        console.warn("[event-handler] failed to parse result", e);
      }
      break;
    }
    case "resource": {
      // **2026-07-08 fix (187b2955):** incremental single-resource
      // ready event. The capability emits one of these as soon as an
      // Agent finishes a Resource, before the whole pipeline drains.
      // We patch it into ``latestPackage.resources`` so the right pane
      // renders the card immediately rather than waiting for the
      // final ``RESULT`` event (which may never arrive if a later
      // stage fails or the 600s timeout fires).
      handleIncrementalResource(streamEv, context);
      break;
    }
    case "error": {
      const structuredError = parseStructuredError(streamEv.metadata.error);
      const errorText = structuredError
        ? `错误 [${structuredError.code}]: ${structuredError.message}`
        : `错误: ${streamEv.content || "未知错误"}`;
      useTutorStore.getState().addMessage({
        role: "system",
        content: errorText,
        stage: streamEv.stage,
        metadata: {
          ...streamEv.metadata,
          ...(structuredError ? { error: structuredError } : {}),
          source: streamEv.source,
          job_id: jobId,
        },
      });
      break;
    }
    case "job_terminal": {
      const md = streamEv.metadata as Record<string, unknown> | undefined;
      const contract = md?.contract as Record<string, unknown> | undefined;
      if (contract && typeof contract === "object") {
        routeResult(contract, streamEv, context);
      }

      // **2026-07-08 fix (fdb26152):** when the contract status is
      // FAILED or PARTIAL, ``routeResult`` won't find a ``package`` to
      // attach (it lives in the final ``RESULT`` event, which never
      // fired). We must fall back to ``contract.partial_artifacts``
      // so the right pane shows the resources the capability had
      // already streamed before the timeout.
      const partial = Array.isArray(contract?.partial_artifacts)
        ? contract.partial_artifacts
        : [];
      const contractStatus =
        typeof contract?.status === "string"
          ? (contract.status as string)
          : "";
      const hasPackage =
        typeof contract?.package === "object" && contract.package !== null;
      if (
        contract &&
        (contractStatus === "failed" || contractStatus === "partial") &&
        !hasPackage &&
        partial.length > 0
      ) {
        buildPartialPackageFromContract(contract, partial, streamEv, context);
      }
      // Persist the assistant message into the active conversation
      // so the sidebar's message_count updates in real time
      // (DeepSeek-style). The contract is the canonical job result
      // payload from JobRunner; its ``assistant_message`` field is
      // what we want to show in the conversation history.
      const assistantText =
        contract && typeof contract === "object"
          ? typeof contract.assistant_message === "string"
            ? (contract.assistant_message as string)
            : typeof contract.message === "string"
              ? (contract.message as string)
              : ""
          : "";
      const state = useTutorStore.getState();
      const persistenceUserId = context.userId || state.userId;
      const persistenceSessionId = authoritativeSessionId || state.sessionId;
      if (persistenceUserId && persistenceSessionId) {
        const cap =
          typeof contract?.capability === "string"
            ? (contract.capability as string)
            : null;
        if (assistantText) {
          persistConversationMessage(
            context.appendConversationMessage,
            persistenceUserId,
            persistenceSessionId,
            {
              role: "assistant",
              content: assistantText,
              job_id: jobId,
              capability: cap,
              metadata: {
                job_id: jobId,
                capability: cap,
                client_message_id: `terminal:${jobId}`,
              },
            },
            "assistant",
          );
        }
        const workflow = state.messages.find((message) => message.id === `workflow:${jobId}`);
        if (!hadWorkflowTimeline && workflow) {
          persistConversationMessage(
            context.appendConversationMessage,
            persistenceUserId,
            persistenceSessionId,
            {
              role: workflow.role as ConversationMessageInput["role"],
              content: workflow.content,
              job_id: jobId,
              capability: cap,
              metadata: workflow.metadata,
            },
            "workflow_timeline",
          );
        }
      }
      // Always clear the legacy single-activeTurn indicator. The new
      // job-reducer model doesn't touch activeTurn.phase, but
      // ChatMessages still shows "正在调用 Agent…" while phase !==
      // "idle" — so a stale phase from before the merge / a phase set
      // by a legacy code path would otherwise hang forever.
      useTutorStore.getState().completeActiveTurn(
        contract ?? null,
        null,
      );
      break;
    }
    // "done" and "cancelled" are no longer required for the visible
    // assistant message — the job_terminal event from JobRunner carries
    // the contract with the canonical assistant_message. We still call
    // completeActiveTurn so legacy single-turn consumers don't hang.
    case "done": {
      useTutorStore.getState().completeActiveTurn(null, null);
      break;
    }
    default:
      break;
  }
}

function terminalAssistantMessage(
  contract: Record<string, unknown> | undefined,
  jobId: string,
): ConversationMessageInput | null {
  if (!contract) return null;
  const content =
    typeof contract.assistant_message === "string"
      ? contract.assistant_message
      : typeof contract.message === "string"
        ? contract.message
        : "";
  if (!content) return null;
  const capability =
    typeof contract.capability === "string" ? contract.capability : null;
  return {
    role: "assistant",
    content,
    job_id: jobId,
    capability,
    metadata: { job_id: jobId, capability, client_message_id: `terminal:${jobId}` },
  };
}

function terminalWorkflowMessage(
  contract: Record<string, unknown> | undefined,
  jobId: string,
  event: StreamEvent,
  stageHistory: InactiveStageHistory | undefined,
): ConversationMessageInput | null {
  const status = contract?.status;
  if (!hasTerminalWorkflowStatus(contract)) return null;
  const job: ClientJob = {
    job_id: jobId,
    capability: typeof contract?.capability === "string" ? contract.capability : "",
    status: status as ClientJob["status"],
    message_preview: "",
    submitted_at: Date.now(),
    started_at: null,
    finished_at: event.timestamp ? event.timestamp * 1000 : Date.now(),
    last_seq: event.seq ?? 0,
    events: [...(stageHistory?.events ?? []), event],
    result: null,
    error: null,
    event_count: 1,
    seen_event_ids: new Set(event.event_id ? [event.event_id] : []),
    text_buffer: "",
    thinking_buffer: "",
    stage: "",
    open_stages: stageHistory?.openStages ?? [],
  };
  const workflow = workflowMessage(job, status as import("./types").JobTerminalStatus);
  return {
    role: "assistant",
    content: workflow.content,
    job_id: jobId,
    capability: job.capability || null,
    metadata: workflow.metadata,
  };
}

function hasTerminalWorkflowStatus(contract: Record<string, unknown> | undefined): boolean {
  return ["succeeded", "partial", "failed", "cancelled"].includes(contract?.status as string);
}

function stageHistoryKey(sessionId: string, jobId: string): string {
  return `${sessionId}:${jobId}`;
}

function recordInactiveStage(sessionId: string, jobId: string, event: StreamEvent): void {
  if ((event.type !== "stage_start" && event.type !== "stage_end") || !event.stage) return;
  const key = stageHistoryKey(sessionId, jobId);
  const current = inactiveStageHistory.get(key) ?? { events: [], openStages: [] };
  const events = event.type === "stage_start" ? [...current.events, event] : current.events;
  let openStages = current.openStages;
  if (event.type === "stage_start") {
    openStages = [...openStages, event.stage];
  } else {
    const index = openStages.lastIndexOf(event.stage);
    if (index >= 0) {
      openStages = [...openStages.slice(0, index), ...openStages.slice(index + 1)];
    }
  }
  inactiveStageHistory.set(key, { events, openStages });
  while (inactiveStageHistory.size > MAX_INACTIVE_STAGE_HISTORIES) {
    const oldest = inactiveStageHistory.keys().next().value;
    if (oldest === undefined) break;
    inactiveStageHistory.delete(oldest);
  }
}

function takeInactiveStageHistory(
  sessionId: string,
  jobId: string,
): InactiveStageHistory | undefined {
  const key = stageHistoryKey(sessionId, jobId);
  const history = inactiveStageHistory.get(key);
  inactiveStageHistory.delete(key);
  return history;
}

function getOrCreateInactiveTerminalPersistence(
  contract: Record<string, unknown> | undefined,
  jobId: string,
  userId: string,
  sessionId: string,
  event: StreamEvent,
  stageHistory: InactiveStageHistory | undefined,
): InactiveTerminalPersistence | null {
  const key = stageHistoryKey(sessionId, jobId);
  const existing = inactiveTerminalPersistence.get(key);
  if (existing) return existing;
  const workflow = terminalWorkflowMessage(contract, jobId, event, stageHistory);
  if (!workflow) return null;
  const assistant = terminalAssistantMessage(contract, jobId);
  const persistence: InactiveTerminalPersistence = {
    userId,
    sessionId,
    assistant,
    workflow,
    assistantDone: assistant === null,
    workflowDone: false,
    assistantInFlight: false,
    workflowInFlight: false,
    finalizedAt: null,
  };
  inactiveTerminalPersistence.set(key, persistence);
  while (inactiveTerminalPersistence.size > MAX_INACTIVE_TERMINAL_RECORDS) {
    const oldest = inactiveTerminalPersistence.keys().next().value;
    if (oldest === undefined) break;
    inactiveTerminalPersistence.delete(oldest);
  }
  return persistence;
}

function attemptInactiveTerminalPersistence(
  key: string,
  persistence: InactiveTerminalPersistence,
  appendConversationMessage?: ConversationMessageAppender,
): void {
  if (!persistence.assistantDone && !persistence.assistantInFlight && persistence.assistant) {
    persistence.assistantInFlight = true;
    void persistConversationMessage(
      appendConversationMessage,
      persistence.userId,
      persistence.sessionId,
      persistence.assistant,
      "assistant",
    ).then((success) => {
      persistence.assistantInFlight = false;
      if (success) persistence.assistantDone = true;
      finalizeInactiveTerminalPersistence(key, persistence);
    });
  }
  if (!persistence.workflowDone && !persistence.workflowInFlight) {
    persistence.workflowInFlight = true;
    void persistConversationMessage(
      appendConversationMessage,
      persistence.userId,
      persistence.sessionId,
      persistence.workflow,
      "workflow_timeline",
    ).then((success) => {
      persistence.workflowInFlight = false;
      if (success) persistence.workflowDone = true;
      finalizeInactiveTerminalPersistence(key, persistence);
    });
  }
}

function finalizeInactiveTerminalPersistence(
  key: string,
  persistence: InactiveTerminalPersistence,
): void {
  if (inactiveTerminalPersistence.get(key) !== persistence) return;
  if (persistence.assistantDone && persistence.workflowDone && persistence.finalizedAt === null) {
    persistence.finalizedAt = Date.now();
  }
}

function persistConversationMessage(
  adapter: ConversationMessageAppender | undefined,
  userId: string,
  sessionId: string,
  message: ConversationMessageInput,
  label: string,
): Promise<boolean> {
  const pending = adapter
    ? Promise.resolve().then(() => adapter(userId, sessionId, message))
    : import("./api").then(({ appendConversationMessage }) =>
        appendConversationMessage(userId, sessionId, message),
      );
  return pending.then(
    () => true,
    (error: unknown) => {
      console.warn(`appendConversationMessage(${label}) failed`, error);
      return false;
    },
  );
}

function parseStructuredError(value: unknown): StructuredError | null {
  if (!isRecord(value)) return null;
  if (typeof value.code !== "string" || typeof value.message !== "string") {
    return null;
  }
  return {
    code: value.code,
    message: value.message,
    ...(Object.prototype.hasOwnProperty.call(value, "details")
      ? { details: value.details }
      : {}),
  };
}

function routeResult(
  payload: Record<string, unknown>,
  ev: StreamEvent,
  context: StreamDispatchContext,
): void {
  const store = useTutorStore.getState();

  // Resource generation result
  if (payload.package && payload.summary) {
    if (isUsableResourcePackage(payload.package)) {
      store.setLatestPackage(payload.package);
    } else {
      scheduleResourcePackageRecovery(
        packageIdFromValue(payload.package),
        context,
      );
    }
    return;
  }

  // Tutoring result
  if (payload.understanding && payload.answer) {
    store.setTutorResult(
      payload.understanding as QuestionUnderstanding,
      payload.answer as TutoringAnswer,
      (payload.enrichments || []) as EnrichmentSuggestion[],
    );
    return;
  }

  // Assessment result
  if (payload.report && payload.strategy) {
    store.setLatestAssessment(payload.report as AssessmentReport);
    store.setLatestStrategy(payload.strategy as StrategyDecision);
    return;
  }

  // KG plan (if returned directly)
  if (payload.path_id && payload.nodes) {
    store.setPlannedPath(payload as unknown as PlannedPath);
    return;
  }

  // Fallback: attach the raw payload to the active turn result
  useTutorStore.setState((s) => ({
    activeTurn: { ...s.activeTurn, result: payload },
  }));
}

/**
 * **2026-07-08 fix (187b2955):** merge one ``RESOURCE`` event into the
 * current ``latestPackage`` so the right-pane card appears immediately.
 *
 * The backend streams ``RESOURCE`` events with the full :class:`Resource`
 * payload under ``metadata.resource``. We append-or-replace by
 * ``resource_id`` so a duplicate ``RESOURCE`` (which can happen if the
 * capability emits one for the same resource after re-rendering a
 * video) doesn't create ghost cards.
 *
 * If no ``latestPackage`` exists yet, we synthesise a minimal one so
 * the user still sees the card even before the final ``RESULT`` event.
 */
/**
 * **2026-07-08 fix (fdb26152):** reconstruct a minimal
 * :class:`ResourcePackage` from ``contract.partial_artifacts`` when the
 * capability never emitted a final ``RESULT`` event (timeout / crash
 * / cancellation). The right pane needs *something* to show — even
 * 6 stub cards are better than an empty pane.
 *
 * **2026-07-08 fix (bbf6ddbf trace):** before this fix the function
 * replaced ``latestPackage.resources`` wholesale with placeholder
 * stubs (``content = "此资源在任务超时前未完整生成，点击查看详情"``),
 * even when the capability had already streamed real ``RESOURCE``
 * events for those resources. The right pane then showed
 * "未完整生成" for ALL five resources, including the four that had
 * real content a moment earlier. We now MERGE: real resources
 * already in ``latestPackage.resources`` (with matching
 * ``resource_id``) are preserved verbatim; placeholder stubs are
 * only synthesised for resource_ids that never streamed an
 * incremental ``RESOURCE`` event. When ``latestPackage`` is empty
 * (no incremental events fired at all) the old all-placeholder
 * behaviour is preserved.
 */
function buildPartialPackageFromContract(
  contract: Record<string, unknown>,
  partial: unknown[],
  ev: StreamEvent,
  context: StreamDispatchContext,
): void {
  const store = useTutorStore.getState();
  const existingPackage = store.latestPackage;
  if (isUsableCanonicalResourcePackage(existingPackage)) return;
  const placeholderPackageId =
    typeof contract.job_id === "string"
      ? `partial-${contract.job_id}`
      : `partial-${ev.event_id}`;

  // Index existing real resources by id so we can preserve them.
  const existingResources = Array.isArray(
    (existingPackage as { resources?: unknown[] } | null)?.resources,
  )
    ? ((existingPackage as unknown as { resources: unknown[] }).resources)
    : [];
  const existingById = new Map<string, Record<string, unknown>>();
  for (const r of existingResources) {
    if (r && typeof r === "object") {
      const obj = r as Record<string, unknown>;
      if (typeof obj.resource_id === "string") {
        existingById.set(obj.resource_id, obj);
      }
    }
  }

  // **2026-07-08 fix (039b4a70 trace):** ``contract.partial_artifacts``
  // can carry the same ``resource_id`` twice when both ``manim_video``
  // (inline emit) and ``resource_capability`` (as_completed yield)
  // fire ``RESOURCE`` for one video. The backend now dedups at the
  // runner level, but we dedup here too as defense-in-depth — a
  // duplicate ``resource_id`` in ``latestPackage.resources`` would
  // trigger React's "Encountered two children with the same key"
  // error and crash the right pane.
  const resources: Record<string, unknown>[] = [];
  const seenIds = new Set<string>();
  let preservedCount = 0;
  let hasMalformedPartial = false;
  for (const entry of partial) {
    if (!isRecord(entry)) {
      hasMalformedPartial = true;
      continue;
    }
    const p = entry as Record<string, unknown>;
    const rid =
      typeof p.resource_id === "string" ? (p.resource_id as string) : "";
    const resourceType =
      typeof p.resource_type === "string" ? p.resource_type.trim() : "";
    if (!rid.trim() || !resourceType) {
      hasMalformedPartial = true;
      continue;
    }
    if (rid) {
      if (seenIds.has(rid)) {
        // Already added a resource for this id; skip the dup.
        continue;
      }
      seenIds.add(rid);
    }
    // Preserve real resource if ``handleIncrementalResource`` already
    // delivered its full payload. This is the bbf6ddbf fix: previously
    // we synthesised a stub even when a real Resource was sitting in
    // ``latestPackage.resources`` waiting to be rendered.
    if (rid && existingById.has(rid)) {
      resources.push(existingById.get(rid)!);
      preservedCount++;
      continue;
    }
    resources.push({
      resource_id: rid,
      type: resourceType,
      title:
        typeof p.title === "string"
          ? (p.title as string)
          : `未命名 ${p.resource_type ?? "资源"}`,
      // No full content from a partial artifact — the UI shows a hint.
      content: "（此资源在任务超时前未完整生成，点击查看详情）",
      format_specific: resourceType === "exercise" ? { questions: [] } : {},
      topic: "",
      difficulty: 3,
      estimated_minutes: 5,
      prerequisites: [],
      generated_by: [],
      confidence_score: 0,
      tags: [],
      metadata: {
        ...((p.metadata as Record<string, unknown>) ?? {}),
        partial: true,
      },
    });
  }

  const partialPackage = {
    package_id: placeholderPackageId,
    topic: typeof ev.metadata?.job_id === "string" ? "" : "",
    resources,
    created_at: new Date().toISOString(),
    target_profile_snapshot: {},
    learning_path_summary: {},
    generated_by: [],
    metadata: {
      partial: true,
      job_id: typeof contract.job_id === "string" ? (contract.job_id as string) : "",
      contract_status:
        typeof contract.status === "string" ? (contract.status as string) : "",
      // Telemetry: how many of the partial_artifacts were already
      // covered by incremental RESOURCE events.
      preserved_count: preservedCount,
      placeholder_count: resources.length - preservedCount,
      display_summary: `部分生成：${resources.length} 项资源（任务未完成）`,
    },
  };
  if (!hasMalformedPartial && isUsableResourcePackage(partialPackage)) {
    store.setLatestPackage(partialPackage);
    return;
  }
  scheduleResourcePackageRecovery(
    packageIdFromValue(contract) ?? packageIdFromValue(ev.metadata),
    context,
  );
}

function handleIncrementalResource(
  ev: StreamEvent,
  context: StreamDispatchContext,
): void {
  const md = (ev.metadata ?? {}) as Record<string, unknown>;
  const raw = md.resource as Record<string, unknown> | undefined;
  if (!raw || typeof raw !== "object") return;
  if (!isUsableStreamedResource(raw)) {
    scheduleResourcePackageRecovery(packageIdFromValue(raw) ?? packageIdFromValue(md), context);
    return;
  }
  const resourceId =
    typeof raw.resource_id === "string"
      ? (raw.resource_id as string)
      : typeof md.resource_id === "string"
        ? (md.resource_id as string)
        : null;
  if (!resourceId) return;

  const store = useTutorStore.getState();
  const existing = store.latestPackage;

  // Build (or reuse) a placeholder package so the right pane has a
  // stable ``package_id`` to switch against.
  const placeholderPackageId =
    existing?.package_id ??
    (typeof ev.metadata?.job_id === "string"
      ? `pending-${ev.metadata.job_id}`
      : `pending-${ev.event_id}`);

  const existingResources = Array.isArray(existing?.resources)
    ? [...(existing!.resources as unknown[])]
    : [];
  // **2026-07-08 fix (039b4a70 trace):** dedup by resource_id. The
  // capability now emits ``RESOURCE`` for the video TWICE in the
  // happy path — once from ``manim_video`` (inline emit at agent
  // return), then again from ``_generate_parallel`` (as_completed
  // yield). The two events land back-to-back (seq=62 and seq=63
  // in the trace) with the same resource_id. The dedup-by-index
  // path below used to be correct but if existingResources already
  // contains a placeholder copy (from an earlier code path), the
  // findIndex lookup matches the placeholder not the real resource
  // and we'd push a duplicate. Walk the array once, replace by id,
  // and bail if a real copy is already present.
  const existingIndex = existingResources.findIndex(
    (candidate) =>
      isRecord(candidate) && candidate.resource_id === resourceId,
  );
  if (existingIndex >= 0) {
    const current = existingResources[existingIndex];
    const merged = mergeIncrementalVideoSnapshot(current, raw);
    if (merged && existing) {
      const resources = [...existingResources];
      resources[existingIndex] = merged;
      store.setLatestPackage({
        ...existing,
        resources: resources as never,
      });
    }
    return;
  }
  // Strip any prior partial placeholders for this id before push.
  const filtered = existingResources.filter(
    (candidate) =>
      !(
        isRecord(candidate) && candidate.resource_id === resourceId
      ),
  );
  filtered.push(raw);
  store.setLatestPackage({
    package_id: placeholderPackageId,
    topic:
      typeof raw.topic === "string"
        ? (raw.topic as string)
        : existing?.topic ?? "",
    resources: filtered as never,
    created_at: existing?.created_at ?? new Date().toISOString(),
    target_profile_snapshot:
      existing?.target_profile_snapshot ?? {},
    learning_path_summary:
      existing?.learning_path_summary ?? {},
    generated_by: existing?.generated_by ?? [],
    metadata: {
      ...(existing?.metadata ?? {}),
      incremental: true,
      display_summary:
        typeof existing?.metadata?.display_summary === "string"
          ? existing.metadata.display_summary
          : `已生成 ${filtered.length} 项资源`,
    },
  });
}

function mergeIncrementalVideoSnapshot(
  current: unknown,
  incoming: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!isRecord(current) || incoming.type !== "video") return null;
  const currentFormat = isRecord(current.format_specific)
    ? current.format_specific
    : {};
  const incomingFormat = isRecord(incoming.format_specific)
    ? incoming.format_specific
    : {};
  const currentRevision =
    typeof currentFormat.source_revision === "number"
      ? currentFormat.source_revision
      : 0;
  const incomingRevision =
    typeof incomingFormat.source_revision === "number"
      ? incomingFormat.source_revision
      : 0;
  if (incomingRevision < currentRevision) return null;
  if (
    incomingRevision === currentRevision &&
    !isEqualRevisionVideoAdvance(currentFormat, incomingFormat)
  ) {
    return null;
  }

  const repairHistory = mergeRepairHistory(
    currentFormat.repair_history,
    incomingFormat.repair_history,
  );
  return {
    ...current,
    ...incoming,
    metadata: {
      ...(isRecord(current.metadata) ? current.metadata : {}),
      ...(isRecord(incoming.metadata) ? incoming.metadata : {}),
    },
    format_specific: {
      ...currentFormat,
      ...incomingFormat,
      ...(repairHistory.length > 0 ? { repair_history: repairHistory } : {}),
    },
  };
}

function isEqualRevisionVideoAdvance(
  current: Record<string, unknown>,
  incoming: Record<string, unknown>,
): boolean {
  const currentRepairJob = stringField(current.repair_job_id);
  const incomingRepairJob = stringField(incoming.repair_job_id);
  if (currentRepairJob || incomingRepairJob) {
    if (!currentRepairJob && incomingRepairJob) {
      return (
        current.render_status === "failed" &&
        ["pending", "running"].includes(stringField(incoming.repair_status))
      );
    }
    if (!currentRepairJob || !incomingRepairJob) return false;
    if (currentRepairJob === incomingRepairJob) {
      return sameJobStatusAdvances(
        stringField(current.repair_status),
        stringField(incoming.repair_status),
        ["pending", "running"],
        ["ready", "failed"],
      );
    }
    if (historyTerminalizes(incoming.repair_history, currentRepairJob)) {
      return true;
    }
    if (historyTerminalizes(current.repair_history, incomingRepairJob)) {
      return false;
    }
    return false;
  }

  const currentRenderJob = stringField(current.render_job_id);
  const incomingRenderJob = stringField(incoming.render_job_id);
  if (!currentRenderJob || currentRenderJob !== incomingRenderJob) return false;
  return sameJobStatusAdvances(
    stringField(current.render_status),
    stringField(incoming.render_status),
    ["pending", "rendering", "running"],
    ["ready", "failed"],
  );
}

function sameJobStatusAdvances(
  currentStatus: string,
  incomingStatus: string,
  activeOrder: string[],
  terminalStatuses: string[],
): boolean {
  const currentTerminal = terminalStatuses.includes(currentStatus);
  const incomingTerminal = terminalStatuses.includes(incomingStatus);
  if (currentTerminal) return false;
  if (incomingTerminal) return true;
  const currentIndex = activeOrder.indexOf(currentStatus);
  const incomingIndex = activeOrder.indexOf(incomingStatus);
  return currentIndex >= 0 && incomingIndex >= currentIndex;
}

function historyTerminalizes(value: unknown, jobId: string): boolean {
  return Array.isArray(value) && value.some(
    (record) =>
      isRecord(record) &&
      record.job_id === jobId &&
      (record.status === "ready" || record.status === "failed"),
  );
}

function mergeRepairHistory(current: unknown, incoming: unknown): unknown[] {
  const records = [
    ...(Array.isArray(current) ? current : []),
    ...(Array.isArray(incoming) ? incoming : []),
  ].filter(isRecord);
  const merged = new Map<string, Record<string, unknown>>();
  for (const record of records) {
    const key = JSON.stringify([
      record.job_id,
      record.failed_revision,
      record.status,
    ]);
    merged.set(key, record);
  }
  return [...merged.values()].slice(-10);
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function packageIdFromValue(value: unknown): string | null {
  if (!isRecord(value)) return null;
  const direct = typeof value.package_id === "string" ? value.package_id.trim() : "";
  if (direct) return direct;
  const metadata = isRecord(value.metadata) ? value.metadata : null;
  const nested = typeof metadata?.package_id === "string" ? metadata.package_id.trim() : "";
  return nested || null;
}

function scheduleResourcePackageRecovery(
  packageId: string | null,
  context: StreamDispatchContext,
): void {
  const userId = context.userId || useTutorStore.getState().userId;
  if (!userId || !packageId) return;

  const key = JSON.stringify([userId, packageId]);
  if (resourceRecoveryInFlight.has(key)) return;

  const recovery = Promise.resolve()
    .then(() => getResourcePackageDetail(userId, packageId))
    .then((pkg) => {
      const current = useTutorStore.getState().latestPackage;
      if (
        isUsableResourcePackage(pkg) &&
        pkg.package_id === packageId &&
        !isUsableCanonicalResourcePackage(current)
      ) {
        useTutorStore.getState().setLatestPackage(pkg);
      }
    })
    .catch((error) => {
      console.warn("[event-handler] resource package recovery failed", error);
    })
    .finally(() => {
      resourceRecoveryInFlight.delete(key);
    });
  resourceRecoveryInFlight.set(key, recovery);
}

function isUsableCanonicalResourcePackage(value: unknown): value is ResourcePackage {
  if (!isUsableResourcePackage(value)) return false;
  const metadata = isRecord(value.metadata) ? value.metadata : {};
  return metadata.incremental !== true && metadata.partial !== true;
}

/**
 * **2026-07-09 fix:** synthesise a structured workflow timeline
 * message from the in-memory ``ClientJob.events[]`` plus any
 * ``partial_artifacts`` emitted in the contract. Returns ``null`` if
 * there's not enough information to bother writing — empty
 * timelines are worse than nothing.
 *
 * Format (Chinese, mirrors the StageIndicator labels so the user
 * sees the same vocabulary across the UI):
 *
 *   ## 工作流程 · 资源生成
 *   状态: 失败 · 117 事件
 *   - ✅ 意图理解
 *   - ✅ 加载画像
 *   - ✅ 资源规划
 *   - ✅ 内容生成
 *   - ✅ 质量审核
 *   - ⏳ Manim 渲染（未完成）
 *
 *   ## 已生成资源（部分）
 *   - 概念讲解
 *   - 公式推导
 *   - 代码实操（未完成）
 */
function buildWorkflowTimeline(
  job: ClientJob,
  partialResources: Array<Record<string, unknown>>,
): string | null {
  // Walk ``open_stages`` first (current deep-nested stack),
  // then fall back to ``job.stage`` for backward-compat with
  // jobs whose events were trimmed. Combine with stage_start
  // events to enumerate everything that ran.
  const stageNames: string[] = [];
  for (const ev of job.events || []) {
    if (
      ev &&
      typeof ev === "object" &&
      (ev as StreamEvent).type === "stage_start" &&
      typeof (ev as StreamEvent).stage === "string" &&
      (ev as StreamEvent).stage
    ) {
      const s = (ev as StreamEvent).stage as string;
      if (!stageNames.includes(s)) stageNames.push(s);
    }
  }

  // Translate known stages to user-facing Chinese labels. The
  // translation table mirrors ``StageIndicator.STAGE_LABELS``.
  const STAGE_LABELS: Record<string, string> = {
    intent_understanding: "意图理解",
    profile_loading: "加载画像",
    knowledge_graph_query: "知识图谱查询",
    resource_planning: "资源规划",
    content_and_pedagogy: "内容生成",
    parallel_resource_generation: "多模态生成",
    quality_review: "质量审核",
    anti_hallucination: "事实核查",
    package_assembly: "组装资源包",
    path_integration: "整合学习路径",
    question_understanding: "问题理解",
    context_retrieval: "检索上下文",
    answer_generation: "生成解答",
    multi_modal_enrichment: "推荐补充",
    pedagogy_design: "教学设计",
    reading_compilation: "阅读材料生成",
    exercise_generation: "习题生成",
    mindmap_generation: "思维导图生成",
    video_concept_design: "视频分镜设计",
    video_code_generation: "Manim 代码生成",
    code_generation: "代码生成",
    render: "Manim 渲染",
    persist_and_emit: "持久化",
  };

  // Determine which stages are still open (incomplete). We use the
  // exact ``open_stages`` stack from the reducer (kept consistent
  // even when MAX_EVENTS_PER_JOB trims old events).
  const openSet = new Set<string>(
    Array.isArray(job.open_stages) ? job.open_stages : [],
  );

  if (stageNames.length === 0 && partialResources.length === 0) {
    return null;
  }

  const lines: string[] = [];
  const stageForReadability = job.capability || "任务";
  lines.push(`## 工作流程 · ${stageForReadability}`);
  const statusLabel: Record<string, string> = {
    succeeded: "成功",
    failed: "失败",
    partial: "部分",
    cancelled: "已取消",
    pending: "等待",
    running: "运行",
  };
  const statusText = statusLabel[job.status] || job.status;
  lines.push(
    `状态: ${statusText} · ${job.event_count} 事件${job.finished_at ? ` · 耗时 ${Math.max(0, Math.round((job.finished_at - job.submitted_at) / 1000))}s` : ""}`,
  );
  if (stageNames.length > 0) {
    for (const s of stageNames) {
      const label = STAGE_LABELS[s] || s;
      const done = !openSet.has(s);
      lines.push(`- ${done ? "✅" : "⏳"} ${label}`);
    }
  }
  // Highlight anything still open at terminal time — guarantees
  // the user can see why the job didn't fully complete.
  const stillOpen = Array.from(openSet).filter((s) => s);
  if (stillOpen.length > 0) {
    lines.push("");
    lines.push("⚠️ 未完成阶段：");
    for (const s of stillOpen) {
      const label = STAGE_LABELS[s] || s;
      lines.push(`- ⏳ ${label}`);
    }
  }

  if (partialResources.length > 0) {
    lines.push("");
    const heading =
      job.status === "succeeded"
        ? "## 生成的资源"
        : "## 已生成资源（部分）";
    lines.push(heading);
    // Deduplicate by resource_id; fallback to title for entries
    // where id is missing.
    const seenIds = new Set<string>();
    for (const r of partialResources) {
      const id =
        typeof r.resource_id === "string" ? (r.resource_id as string) : "";
      if (id && seenIds.has(id)) continue;
      if (id) seenIds.add(id);
      const title =
        typeof r.title === "string" && r.title
          ? (r.title as string)
          : typeof r.resource_type === "string"
            ? `${r.resource_type} 资源`
            : "未命名资源";
      const statusIcon =
        typeof r.status === "string" &&
        (r.status === "succeeded" ||
          r.status === "completed" ||
          r.status === "ready")
          ? "✅"
          : "⚠️";
      lines.push(`- ${statusIcon} ${title}`);
    }
  }

  return lines.join("\n");
}
