/**
 * TaskProcess view model — one normalized shape for the task-progress
 * card so it renders identically from:
 *
 *   1. the live reducer state (``taskProcessFromJob``) while a job runs;
 *   2. a persisted ``workflow_timeline`` chat message
 *      (``taskProcessFromWorkflowMessage``) after a refresh — written by
 *      the backend runner at terminal time (2026-07-19 plan).
 */

import type { ClientJob } from "./job-reducer";
import type {
  ChatMessage,
  JobTerminalStatus,
  StreamEvent,
  StructuredError,
} from "./types";

export interface TaskProcessStage {
  key: string;
  label: string;
  state: "completed" | "active" | "pending" | "incomplete";
}

export interface TaskProcessData {
  status: "active" | JobTerminalStatus;
  stages: TaskProcessStage[];
  /** Newest-last progress texts (capped to the last 8 for display). */
  progress: string[];
  resourceCount: number;
  startedAt: number | null;
  finishedAt: number | null;
  durationMs: number | null;
  error: StructuredError | null;
}

export const STAGE_LABELS: Record<string, string> = {
  intent: "理解目标",
  understand: "理解目标",
  question: "理解问题",
  profile: "读取学习状态",
  knowledge: "查找课程资料",
  context: "查找课程资料",
  rag: "查找课程资料",
  retrieval: "查找课程资料",
  resource_planning: "整理下一步",
  path: "整理下一步",
  content: "整理讲解",
  pedagogy: "整理讲解",
  answer: "整理讲解",
  exercise: "准备练习",
  reading: "准备学习资料",
  mindmap: "准备学习资料",
  video: "准备可视资料",
  code: "准备示例",
  parallel_resource: "准备学习资料",
  review: "检查内容",
  safety: "检查内容",
  hallucination: "检查内容",
  fact_check: "检查内容",
  assessment: "整理练习结果",
  adaptive: "安排下一步",
  event: "更新学习状态",
  persist: "保存学习记录",
  session_recording: "保存学习记录",
  package: "整理学习资料",
};

export function naturalStage(stage: string) {
  const normalized = stage.toLowerCase();
  for (const [key, label] of Object.entries(STAGE_LABELS)) {
    if (normalized.includes(key)) return label;
  }
  return "准备学习内容";
}

/** Canonical stage order of the resource_generation capability manifest.
 * Only used to render "未开始" chips; other capabilities show the stages
 * seen in events. Keep in sync with
 * ``backend/tutor/capabilities/resource_generation.py`` manifest. */
const RESOURCE_GENERATION_STAGES: string[] = [
  "intent_understanding",
  "profile_loading",
  "knowledge_graph_query",
  "rag_retrieval",
  "web_search",
  "resource_planning",
  "content_and_pedagogy",
  "parallel_resource_generation",
  "quality_review",
  "anti_hallucination",
  "package_assembly",
  "path_integration",
  "persistence",
];

export const MAX_PROGRESS_MESSAGES = 8;

function progressText(event: StreamEvent): string {
  const metadata = event.metadata as Record<string, unknown> | undefined;
  const message = metadata && typeof metadata.message === "string"
    ? metadata.message
    : "";
  return message || event.content || "";
}

/** Collapse stages that map to the same Chinese label (keeps order,
 * last state wins — same as the old ``WorkflowDocument``). */
function dedupeStages(stages: TaskProcessStage[]): TaskProcessStage[] {
  const byLabel = new Map<string, TaskProcessStage>();
  for (const stage of stages) byLabel.set(stage.label, stage);
  return Array.from(byLabel.values());
}

export function taskProcessFromJob(job: ClientJob): TaskProcessData {
  const seen: string[] = [];
  const open = new Set(job.open_stages ?? []);
  const progress: string[] = [];
  let resourceCount = 0;
  for (const event of job.events) {
    if (event.type === "stage_start" && event.stage && !seen.includes(event.stage)) {
      seen.push(event.stage);
    } else if (event.type === "progress") {
      const text = progressText(event).trim();
      if (text) progress.push(text);
    } else if (event.type === "resource") {
      resourceCount += 1;
    }
  }
  const keys: string[] =
    job.capability === "resource_generation"
      ? [...RESOURCE_GENERATION_STAGES]
      : [];
  for (const key of seen) if (!keys.includes(key)) keys.push(key);
  for (const key of open) if (!keys.includes(key)) keys.push(key);

  const stages = dedupeStages(
    keys.map((key) => ({
      key,
      label: naturalStage(key),
      state: open.has(key)
        ? ("active" as const)
        : seen.includes(key)
          ? ("completed" as const)
          : ("pending" as const),
    })),
  );
  return {
    status: "active",
    stages,
    progress: progress.slice(-MAX_PROGRESS_MESSAGES),
    resourceCount,
    startedAt: job.started_at ?? job.submitted_at ?? null,
    finishedAt: job.finished_at ?? null,
    durationMs: null,
    error: job.error ?? null,
  };
}

export function taskProcessFromWorkflowMessage(
  message: ChatMessage,
): TaskProcessData | null {
  const metadata = message.metadata as Record<string, unknown> | undefined;
  if (metadata?.kind !== "workflow_timeline") return null;
  const workflow = metadata.workflow as
    | { status?: unknown; stages?: unknown }
    | undefined;
  if (
    !workflow ||
    !["succeeded", "partial", "failed", "cancelled"].includes(
      String(workflow.status),
    ) ||
    !Array.isArray(workflow.stages)
  ) {
    return null;
  }
  const stages: TaskProcessStage[] = [];
  for (const raw of workflow.stages) {
    if (!raw || typeof raw !== "object") return null;
    const name = (raw as { name?: unknown }).name;
    const status = (raw as { status?: unknown }).status;
    if (
      typeof name !== "string" ||
      !["completed", "incomplete"].includes(String(status))
    ) {
      return null;
    }
    stages.push({
      key: name,
      label: naturalStage(name),
      state: status === "completed" ? "completed" : "incomplete",
    });
  }
  const progressRaw = Array.isArray(metadata.progress_excerpt)
    ? (metadata.progress_excerpt as unknown[])
    : [];
  const progress = progressRaw
    .filter((item): item is string => typeof item === "string")
    .slice(-MAX_PROGRESS_MESSAGES);
  const resources = metadata.resources as { total?: unknown } | undefined;
  const resourceCount =
    resources && typeof resources.total === "number" ? resources.total : 0;
  const durationMs =
    typeof metadata.duration_ms === "number" ? metadata.duration_ms : null;
  return {
    status: workflow.status as JobTerminalStatus,
    stages: dedupeStages(stages),
    progress,
    resourceCount,
    startedAt: null,
    finishedAt: message.timestamp ?? null,
    durationMs,
    error: null,
  };
}

export function formatDuration(durationMs: number): string {
  const seconds = Math.max(0, Math.round(durationMs / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}
