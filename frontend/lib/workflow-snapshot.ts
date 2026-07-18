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

export function workflowMessage(
  job: ClientJob,
  status: JobTerminalStatus,
): ChatMessage {
  return {
    id: `workflow:${job.job_id}`,
    role: "assistant",
    content: "",
    timestamp: job.finished_at ?? Date.now(),
    metadata: {
      kind: "workflow_timeline",
      job_id: job.job_id,
      workflow: buildWorkflowSnapshot(job, status),
    },
  };
}
