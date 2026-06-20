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
      const turn = useTutorStore.getState().activeTurn;
      const result = turn.result;
      useTutorStore.getState().completeActiveTurn(result, null);
      // If completeActiveTurn didn't push a visible assistant message
      // (because text_buffer + thinking_buffer were both empty — the common
      // case for resource_generation, which never streams text), inject a
      // contextual completion message so the user gets visible feedback.
      injectCompletionMessageIfMissing();
      break;
    }
    default:
      break;
  }
}

/**
 * Some capabilities (notably resource_generation) don't emit ``content``
 * events — they only emit stage_start/thinking/result. When such a turn
 * finishes, ``completeActiveTurn`` has nothing to put in an assistant
 * message bubble. To avoid the dialog going silent, generate a one-line
 * summary based on the capability + result payload.
 */
function injectCompletionMessageIfMissing(): void {
  const store = useTutorStore.getState();
  const turn = store.activeTurn;
  const messages = store.messages;

  // Was a message just pushed for this turn? If so, leave it alone.
  const last = messages[messages.length - 1];
  const justAdded =
    last && last.role === "assistant" && last.timestamp >= turn.started_at;
  if (justAdded) return;

  const cap = store.currentCapability ?? "unknown";
  const result = turn.result as Record<string, unknown> | null;

  let content = "";
  switch (cap) {
    case "resource_generation": {
      // result is { package: { resources: [...] }, summary, kg_summary }
      const pkg = result?.package as { resources?: unknown[] } | undefined;
      const count = pkg?.resources?.length ?? 0;
      const summary = (result?.summary as string) || "";
      content =
        count > 0
          ? `✅ 已生成 ${count} 类学习资源${
              summary ? `（${summary}）` : ""
            }，请在右侧面板查看。`
          : "✅ 资源生成任务完成，但未产出资源（请检查 LLM 是否正常工作）。";
      break;
    }
    case "tutoring":
      content =
        "✅ 答疑完成，详见右侧「即时答疑」面板。";
      break;
    case "assessment":
      content =
        "✅ 效果评估完成，详见右侧「效果评估」面板（含 6 维评分 + 自适应策略）。";
      break;
    case "path_planning":
      content = "✅ 学习路径已规划，详见右侧「路径规划」面板。";
      break;
    case "profile":
      content = "✅ 学习画像已更新。";
      break;
    default:
      content = `✅ 任务完成（${cap}）。`;
  }

  store.addMessage({
    role: "assistant",
    agent: cap,
    content,
    metadata: { capability: cap, synthetic: true },
  });
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
