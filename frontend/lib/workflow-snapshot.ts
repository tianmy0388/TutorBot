import type { ClientJob } from "./job-reducer";
import type { ChatMessage, JobTerminalStatus, WorkflowSnapshot } from "./types";

export function buildWorkflowSnapshot(
  job: ClientJob,
  terminalStatus: JobTerminalStatus,
): WorkflowSnapshot {
  const stages: string[] = [];
  const openStages = new Set(job.open_stages ?? []);

  for (const event of job.events) {
    if (event.type === "stage_start" && event.stage && !stages.includes(event.stage)) {
      stages.push(event.stage);
    }
  }
  for (const stage of openStages) {
    if (stage && !stages.includes(stage)) stages.push(stage);
  }

  return {
    status: terminalStatus,
    stages: stages.map((name) => ({
      name,
      status: openStages.has(name) ? "incomplete" : "completed",
    })),
  };
}

function progressExcerpt(job: ClientJob): string[] {
  const messages: string[] = [];
  for (const event of job.events) {
    if (event.type !== "progress") continue;
    const metadata = event.metadata as Record<string, unknown> | undefined;
    const text =
      (metadata && typeof metadata.message === "string"
        ? metadata.message
        : "") || event.content || "";
    if (text.trim()) messages.push(text);
  }
  return messages.slice(-50);
}

function resourceCounts(job: ClientJob): { total: number; succeeded: number } {
  let total = 0;
  let succeeded = 0;
  for (const event of job.events) {
    if (event.type !== "resource") continue;
    total += 1;
    const metadata = event.metadata as Record<string, unknown> | undefined;
    const resource = metadata?.resource as Record<string, unknown> | undefined;
    const formatSpecific = resource?.format_specific as
      | Record<string, unknown>
      | undefined;
    if (!formatSpecific || typeof formatSpecific !== "object" || !formatSpecific.failure) {
      succeeded += 1;
    }
  }
  return { total, succeeded };
}

export function workflowMessage(
  job: ClientJob,
  status: JobTerminalStatus,
): ChatMessage {
  const durationMs =
    job.started_at != null && job.finished_at != null
      ? Math.max(0, job.finished_at - job.started_at)
      : null;
  return {
    id: `workflow:${job.job_id}`,
    role: "assistant",
    content: "",
    timestamp: job.finished_at ?? Date.now(),
    metadata: {
      kind: "workflow_timeline",
      job_id: job.job_id,
      client_message_id: `workflow:${job.job_id}`,
      workflow: buildWorkflowSnapshot(job, status),
      duration_ms: durationMs,
      resources: resourceCounts(job),
      progress_excerpt: progressExcerpt(job),
    },
  };
}
