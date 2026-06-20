/**
 * StreamEvent → Store dispatch logic.
 *
 * The store provides `applyStreamEvent` and `completeActiveTurn`.
 * This module adds the higher-level semantics: detect capability from the
 * event's `source` / `stage`, route RESULT payloads to the right slice,
 * surface errors as chat messages, etc.
 */

import { useTutorStore } from "./store";
import type { StreamEvent } from "./types";
import {
  type AssessmentReport,
  type PlannedPath,
  type ResourcePackage,
  type StrategyDecision,
  type TutoringAnswer,
  type EnrichmentSuggestion,
  type QuestionUnderstanding,
} from "./types";

export function dispatchStreamEvent(ev: StreamEvent): void {
  const store = useTutorStore.getState();
  store.applyStreamEvent(ev);

  switch (ev.type) {
    case "result": {
      // The backend serialises the full capability result in `content` as JSON.
      try {
        const payload = JSON.parse(ev.content);
        routeResult(payload, ev);
      } catch (e) {
        console.warn("[event-handler] failed to parse result", e);
      }
      break;
    }
    case "error": {
      useTutorStore.setState((s) => ({
        activeTurn: { ...s.activeTurn, error: ev.content },
      }));
      useTutorStore.getState().addMessage({
        role: "system",
        content: `错误: ${ev.content}`,
        stage: ev.stage,
        metadata: { ...ev.metadata, source: ev.source },
      });
      break;
    }
    case "done": {
      // Pull current result from the turn
      const result = useTutorStore.getState().activeTurn.result;
      useTutorStore.getState().completeActiveTurn(result, null);
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
    // KG summary for path visualization
    if (payload.kg_summary && typeof payload.kg_summary === "object") {
      // KG path is computed client-side from planPath() too; backend already
      // attached a summary — we keep plannedPath null and let the page call
      // /kg/{course}/plan for the full path.
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
