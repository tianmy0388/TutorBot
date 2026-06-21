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
import type { StreamEvent, WSServerMessage } from "./types";
import {
  type AssessmentReport,
  type PlannedPath,
  type ResourcePackage,
  type StrategyDecision,
  type TutoringAnswer,
  type EnrichmentSuggestion,
  type QuestionUnderstanding,
} from "./types";

/**
 * Compatibility adapter: capabilities that were designed for the
 * single-activeTurn model still emit ``result`` / ``error`` / ``done``
 * / ``cancelled`` events on a per-job basis. We split the dispatch into
 * the job reducer (for ownership and replay) and the result router
 * (for capability-specific payload dispatch).
 */
export function dispatchStreamEvent(
  ev: StreamEvent | WSServerMessage,
): void {
  // Protocol / ack messages (job_submitted, ack, pong) are handled by the
  // WsClient itself; we shouldn't see them here. Defensive no-op.
  if (
    ev.type === "ack" ||
    ev.type === "pong" ||
    ev.type === "job_submitted"
  ) {
    return;
  }
  // Normalise to a strict StreamEvent for the reducer + router.
  const streamEv: StreamEvent = {
    type: ev.type as StreamEvent["type"],
    source: ev.source ?? "",
    stage: ev.stage ?? "",
    content: ev.content ?? "",
    metadata: ev.metadata ?? {},
    session_id: ev.session_id ?? "",
    turn_id: ev.turn_id ?? "",
    seq: ev.seq ?? 0,
    timestamp: ev.timestamp ?? Date.now() / 1000,
    event_id: ev.event_id ?? "",
  };
  const jobId = getJobIdFromEvent(streamEv);
  if (!jobId) {
    useTutorStore.getState().addMessage({
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
        const payload = JSON.parse(streamEv.content);
        routeResult(payload, streamEv);
      } catch (e) {
        console.warn("[event-handler] failed to parse result", e);
      }
      break;
    }
    case "error": {
      useTutorStore.getState().addMessage({
        role: "system",
        content: `错误: ${streamEv.content}`,
        stage: streamEv.stage,
        metadata: { ...streamEv.metadata, source: streamEv.source, job_id: jobId },
      });
      break;
    }
    case "job_terminal": {
      const md = streamEv.metadata as Record<string, unknown> | undefined;
      const contract = md?.contract as Record<string, unknown> | undefined;
      if (contract && typeof contract === "object") {
        routeResult(contract, streamEv);
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
      if (assistantText && state.userId && state.sessionId) {
        const cap =
          typeof contract?.capability === "string"
            ? (contract.capability as string)
            : null;
        void import("./api")
          .then(({ appendConversationMessage }) =>
            appendConversationMessage(state.userId, state.sessionId, {
              role: "assistant",
              content: assistantText,
              job_id: jobId,
              capability: cap,
              metadata: { job_id: jobId, capability: cap },
            }),
          )
          .catch((e) => {
            console.warn("appendConversationMessage(assistant) failed", e);
          });
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

function routeResult(
  payload: Record<string, unknown>,
  ev: StreamEvent,
): void {
  const store = useTutorStore.getState();

  // Resource generation result
  if (payload.package && payload.summary) {
    const pkg = payload.package as ResourcePackage;
    store.setLatestPackage(pkg);
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
