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
import { getResourcePackageDetail } from "./api";
import {
  isUsableResourcePackage,
  isUsableStreamedResource,
} from "./resource-validation";
import type { StreamEvent, StructuredError } from "./types";
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
}

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
    // Events for a non-visible session are dropped. The backend runner
    // persists the terminal workflow/assistant messages itself
    // (2026-07-19 plan), so the browser no longer tracks or POSTs
    // anything for background sessions.
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
      // A resource worker can finish before the whole pipeline drains.
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
      // Always clear the legacy single-activeTurn indicator. The new
      // job-reducer model doesn't touch activeTurn.phase, but
      // Legacy consumers can otherwise keep showing stale progress while phase !==
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
        !historyTerminalizes(current.repair_history, incomingRepairJob) &&
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
