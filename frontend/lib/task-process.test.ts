import { describe, expect, it } from "vitest";

import { createJobState, __test__ } from "./job-reducer";
import {
  formatDuration,
  naturalStage,
  taskProcessFromJob,
  taskProcessFromWorkflowMessage,
} from "./task-process";
import type { ChatMessage, StreamEvent } from "./types";

function streamEvent(partial: Partial<StreamEvent>): StreamEvent {
  return {
    type: "progress",
    source: "test",
    stage: "",
    content: "",
    metadata: {},
    session_id: "s1",
    turn_id: "",
    seq: 1,
    timestamp: 1,
    event_id: `e-${Math.random()}`,
    ...partial,
  };
}

function runningJob() {
  const state = createJobState("job-1", "resource_generation");
  const events: StreamEvent[] = [
    streamEvent({ type: "stage_start", stage: "intent_understanding", seq: 1 }),
    streamEvent({ type: "stage_end", stage: "intent_understanding", seq: 2 }),
    streamEvent({
      type: "progress",
      metadata: { message: "正在理解目标" },
      seq: 3,
    }),
    streamEvent({ type: "stage_start", stage: "rag_retrieval", seq: 4 }),
    streamEvent({
      type: "progress",
      metadata: { message: "正在查找课程资料" },
      seq: 5,
    }),
    streamEvent({ type: "resource", seq: 6 }),
  ];
  let next = state;
  for (const event of events) {
    next = __test__.applyStream(next, { type: "stream", event, job_id: "job-1" });
  }
  return next.jobsById["job-1"];
}

describe("taskProcessFromJob", () => {
  it("builds stage chips with completed/active/pending states", () => {
    const data = taskProcessFromJob(runningJob());
    expect(data.status).toBe("active");
    const byLabel = new Map(data.stages.map((s) => [s.label, s.state]));
    expect(byLabel.get("理解目标")).toBe("completed");
    expect(byLabel.get("查找课程资料")).toBe("active");
    // Known-but-not-yet-started manifest stages show as pending.
    expect(data.stages.some((s) => s.state === "pending")).toBe(true);
    expect(data.progress).toEqual(["正在理解目标", "正在查找课程资料"]);
    expect(data.resourceCount).toBe(1);
  });
});

describe("taskProcessFromWorkflowMessage", () => {
  const message: ChatMessage = {
    id: "workflow:job-9",
    role: "assistant",
    content: "",
    timestamp: 1752000000000,
    metadata: {
      kind: "workflow_timeline",
      job_id: "job-9",
      client_message_id: "workflow:job-9",
      workflow: {
        status: "succeeded",
        stages: [
          { name: "intent_understanding", status: "completed" },
          { name: "video_rendering", status: "incomplete" },
        ],
      },
      duration_ms: 65000,
      resources: { total: 6, succeeded: 5 },
      progress_excerpt: ["第一步", "最后一步"],
    },
  };

  it("renders the persisted shape", () => {
    const data = taskProcessFromWorkflowMessage(message);
    expect(data).not.toBeNull();
    expect(data!.status).toBe("succeeded");
    expect(data!.stages.map((s) => s.state)).toEqual([
      "completed",
      "incomplete",
    ]);
    expect(data!.progress).toEqual(["第一步", "最后一步"]);
    expect(data!.resourceCount).toBe(6);
    expect(data!.durationMs).toBe(65000);
  });

  it("returns null for non-workflow or malformed messages", () => {
    expect(
      taskProcessFromWorkflowMessage({ ...message, metadata: { kind: "x" } }),
    ).toBeNull();
    expect(
      taskProcessFromWorkflowMessage({
        ...message,
        metadata: { kind: "workflow_timeline", workflow: { status: "nope" } },
      }),
    ).toBeNull();
  });
});

describe("helpers", () => {
  it("naturalStage maps stage keys to Chinese labels", () => {
    expect(naturalStage("rag_retrieval")).toBe("查找课程资料");
    expect(naturalStage("something_unknown")).toBe("准备学习内容");
  });

  it("formatDuration renders seconds and minutes", () => {
    expect(formatDuration(42000)).toBe("42 秒");
    expect(formatDuration(65000)).toBe("1 分 5 秒");
  });
});
