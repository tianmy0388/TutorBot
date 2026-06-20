/**
 * Tests for the per-job event reducer.
 *
 * These tests pin the no-output regression (Task 3) and the new contract
 * behaviour: every terminal job MUST surface exactly one visible
 * assistant message sourced from the server's ``JobResultContract`` —
 * never from a client-side heuristic over a single global capability.
 */

import { describe, expect, it } from "vitest";

import {
  MAX_EVENTS_PER_JOB,
  createJobState,
  emptyJobsState,
  getJobIdFromEvent,
  isTerminal,
  reduceJobEvent,
  type JobsState,
  type StreamReducerEvent,
  type TerminalReducerEvent,
} from "./job-reducer";
import type { StreamEvent } from "./types";

function terminalEvent(jobId: string, content: string): TerminalReducerEvent {
  return {
    type: "job_terminal",
    job_id: jobId,
    capability: "resource_generation",
    result: {
      job_id: jobId,
      capability: "resource_generation",
      status: "succeeded",
      assistant_message: content,
      artifacts: [],
      warnings: [],
    },
    timestamp: 1_700_000_000,
    event_id: `terminal_${jobId}_1`,
  };
}

function streamEvent(jobId: string, partial: Partial<StreamEvent> = {}): StreamReducerEvent {
  return {
    type: "stream",
    job_id: jobId,
    event: {
      type: "stage_start",
      source: "agent",
      stage: "intent",
      content: "",
      metadata: { job_id: jobId },
      session_id: "sess",
      turn_id: "",
      seq: 1,
      timestamp: 1_700_000_000,
      event_id: `evt_${jobId}_1`,
      ...partial,
    },
  };
}

describe("job-reducer", () => {
  it("adds a visible assistant message when an async job succeeds", () => {
    const state = createJobState("job-1", "resource_generation");
    const next = reduceJobEvent(
      state,
      terminalEvent("job-1", "已生成 3 项资源"),
    );
    expect(next.jobsById["job-1"].status).toBe("succeeded");
    expect(next.messages.at(-1)?.content).toBe("已生成 3 项资源");
    expect(next.messages.at(-1)?.metadata?.terminal).toBe(true);
    expect(next.messages.at(-1)?.metadata?.job_id).toBe("job-1");
  });

  it("does not treat an older assistant message as output for a new job", () => {
    const state = createJobState(
      "job-2",
      "tutoring",
      [
        {
          id: "old",
          role: "assistant",
          content: "旧回答",
          timestamp: 1,
        },
      ],
    );
    const next = reduceJobEvent(state, terminalEvent("job-2", "新回答"));
    expect(next.messages.map((m) => m.content)).toEqual(["旧回答", "新回答"]);
  });

  it("does not duplicate the assistant message on replay of the same terminal", () => {
    const state = createJobState("job-3", "tutoring");
    const once = reduceJobEvent(state, terminalEvent("job-3", "ok"));
    const twice = reduceJobEvent(once, terminalEvent("job-3", "ok"));
    const assistantCount = twice.messages.filter(
      (m) => m.metadata?.job_id === "job-3" && m.metadata?.terminal === true,
    ).length;
    expect(assistantCount).toBe(1);
  });

  it("dedupes events by event_id", () => {
    const state = createJobState("job-4", "tutoring");
    const next1 = reduceJobEvent(state, streamEvent("job-4"));
    const next2 = reduceJobEvent(next1, streamEvent("job-4")); // same event_id
    expect(next2.jobsById["job-4"].event_count).toBe(
      next1.jobsById["job-4"].event_count,
    );
    expect(next2.jobsById["job-4"].events.length).toBe(
      next1.jobsById["job-4"].events.length,
    );
  });

  it("marks the job as running on first stage_start", () => {
    const state = createJobState("job-5", "tutoring");
    const next = reduceJobEvent(state, streamEvent("job-5"));
    expect(next.jobsById["job-5"].status).toBe("running");
    expect(next.jobsById["job-5"].started_at).not.toBeNull();
  });

  it("partial contract surfaces a partial job with named failed resources", () => {
    const state = createJobState("job-6", "resource_generation");
    const ev: TerminalReducerEvent = {
      type: "job_terminal",
      job_id: "job-6",
      capability: "resource_generation",
      result: {
        job_id: "job-6",
        capability: "resource_generation",
        status: "partial",
        assistant_message: "已生成 2 项资源，1 项失败：video",
        artifacts: [
          {
            resource_type: "document",
            status: "succeeded",
            resource_id: "doc-1",
          },
          {
            resource_type: "exercise",
            status: "succeeded",
            resource_id: "ex-1",
          },
          {
            resource_type: "video",
            status: "failed",
            error: {
              code: "MANIM_RENDER_FAILED",
              message: "渲染失败",
              retryable: true,
            },
          },
        ],
        warnings: [],
      },
      timestamp: 1_700_000_000,
    };
    const next = reduceJobEvent(state, ev);
    expect(next.jobsById["job-6"].status).toBe("partial");
    expect(next.jobsById["job-6"].result?.artifacts?.length).toBe(3);
    expect(next.messages.at(-1)?.content).toContain("失败");
  });

  it("rejects events for unknown jobs without crashing", () => {
    const state = createJobState("job-7", "tutoring");
    const next = reduceJobEvent(state, streamEvent("missing-job"));
    expect(next).toBe(state);
  });

  it("caps per-job events at MAX_EVENTS_PER_JOB", () => {
    const state = createJobState("job-8", "tutoring");
    let next: JobsState = state;
    for (let i = 0; i < MAX_EVENTS_PER_JOB + 10; i++) {
      next = reduceJobEvent(next, {
        type: "stream",
        job_id: "job-8",
        event: {
          type: "stage_start",
          source: "agent",
          stage: "x",
          content: "",
          metadata: { job_id: "job-8" },
          session_id: "",
          turn_id: "",
          seq: i + 1,
          timestamp: 1_700_000_000,
          event_id: `evt_${i}`,
        },
      });
    }
    expect(next.jobsById["job-8"].events.length).toBe(MAX_EVENTS_PER_JOB);
  });

  it("emptyJobsState starts with no jobs", () => {
    const state = emptyJobsState([
      { id: "x", role: "user", content: "hi", timestamp: 1 },
    ]);
    expect(Object.keys(state.jobsById)).toEqual([]);
    expect(state.messages.length).toBe(1);
  });

  it("getJobIdFromEvent reads metadata.job_id", () => {
    const ev: StreamEvent = {
      type: "content",
      source: "agent",
      stage: "",
      content: "hi",
      metadata: { job_id: "abc" },
      session_id: "",
      turn_id: "",
      seq: 1,
      timestamp: 0,
      event_id: "e1",
    };
    expect(getJobIdFromEvent(ev)).toBe("abc");
  });

  it("getJobIdFromEvent returns null when metadata is missing", () => {
    const ev: StreamEvent = {
      type: "content",
      source: "agent",
      stage: "",
      content: "hi",
      metadata: {},
      session_id: "",
      turn_id: "",
      seq: 1,
      timestamp: 0,
      event_id: "e2",
    };
    expect(getJobIdFromEvent(ev)).toBeNull();
  });

  it("isTerminal identifies terminal statuses", () => {
    expect(isTerminal("pending")).toBe(false);
    expect(isTerminal("running")).toBe(false);
    expect(isTerminal("succeeded")).toBe(true);
    expect(isTerminal("partial")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("cancelled")).toBe(true);
  });

  it("snapshot hydrates a known terminal job without duplicating the message", () => {
    const state = createJobState("job-9", "tutoring");
    const terminal = reduceJobEvent(state, terminalEvent("job-9", "hi"));
    const snapshot = reduceJobEvent(terminal, {
      type: "snapshot",
      job: {
        job_id: "job-9",
        capability: "tutoring",
        status: "succeeded",
        message_preview: "hi",
        finished_at: new Date(
          terminal.jobsById["job-9"].finished_at ?? Date.now(),
        ).toISOString(),
        last_seq: 5,
        events: [],
        result: terminal.jobsById["job-9"].result,
        event_count: 5,
      },
    });
    const assistantCount = snapshot.messages.filter(
      (m) => m.metadata?.job_id === "job-9" && m.metadata?.terminal === true,
    ).length;
    expect(assistantCount).toBe(1);
  });

  it("snapshot inserts a terminal message for a job we never saw running", () => {
    const state = emptyJobsState();
    const next = reduceJobEvent(state, {
      type: "snapshot",
      job: {
        job_id: "job-10",
        capability: "tutoring",
        status: "succeeded",
        message_preview: "from snapshot",
        result: {
          job_id: "job-10",
          capability: "tutoring",
          status: "succeeded",
          assistant_message: "从快照恢复的消息",
          artifacts: [],
          warnings: [],
        },
        event_count: 0,
        last_seq: 0,
      },
    });
    expect(next.jobsById["job-10"]?.status).toBe("succeeded");
    expect(next.messages.at(-1)?.content).toBe("从快照恢复的消息");
  });

  it("submit before any events registers the job in pending state", () => {
    const state = emptyJobsState();
    const next = reduceJobEvent(state, {
      type: "submit",
      job_id: "job-11",
      capability: "tutoring",
      message_preview: "hi",
    });
    expect(next.jobsById["job-11"]?.status).toBe("pending");
    expect(next.jobOrder[0]).toBe("job-11");
  });

  it("terminal error contract maps to FAILED status", () => {
    const state = createJobState("job-12", "tutoring");
    const next = reduceJobEvent(state, {
      type: "job_terminal",
      job_id: "job-12",
      capability: "tutoring",
      result: {
        job_id: "job-12",
        capability: "tutoring",
        status: "failed",
        assistant_message: "任务失败：boom",
        error: {
          code: "CAPABILITY_ERROR",
          message: "boom",
          retryable: true,
        },
        artifacts: [],
        warnings: [],
      },
    });
    expect(next.jobsById["job-12"].status).toBe("failed");
    expect(next.jobsById["job-12"].error).toBe("boom");
  });
});
