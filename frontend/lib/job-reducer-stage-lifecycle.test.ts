/**
 * Regression test for 585f367d: stage chip loops after job_terminal.
 *
 * Pre-fix, ``applyStream`` in ``job-reducer.ts`` only handled
 * ``stage_start`` (writing the stage name into ``ClientJob.stage``).
 * There was no symmetric ``stage_end`` branch, so once a stage was
 * set it stayed — even after ``job_terminal`` arrived. Combined with
 * ``StageProgress`` reading ``starts[starts.length-1]`` as the
 * "active" stage, every trailing unmatched ``stage_start`` looked
 * like an active, in-progress stage with a spinner forever.
 *
 * After the fix:
 *   - ``applyStream`` maintains a stack of open stages.
 *   - ``stage_start`` pushes onto the stack.
 *   - ``stage_end`` pops matching stage from the stack.
 *   - ``job.stage`` is the top of the stack (empty string when none).
 *   - ``applyTerminal`` clears the stack so a fresh job starts clean.
 */

import { describe, expect, it } from "vitest";

import {
  createJobState,
  reduceJobEvent,
  type StreamReducerEvent,
  type TerminalReducerEvent,
} from "./job-reducer";
import type { StreamEvent } from "./types";

function streamEvent(
  jobId: string,
  partial: Partial<StreamEvent> = {},
): StreamReducerEvent {
  return {
    type: "stream",
    job_id: jobId,
    event: {
      type: "stage_start",
      source: "test",
      stage: "",
      content: "",
      metadata: { job_id: jobId },
      session_id: "s",
      turn_id: "t",
      seq: 1,
      timestamp: 1_700_000_000,
      event_id: `evt-${Math.random().toString(36).slice(2)}`,
      ...partial,
    },
  };
}

function terminalEvent(
  jobId: string,
  status: "succeeded" | "failed" | "partial" | "cancelled",
  content: string,
): TerminalReducerEvent {
  return {
    type: "job_terminal",
    job_id: jobId,
    capability: "resource_generation",
    result: {
      job_id: jobId,
      capability: "resource_generation",
      status,
      assistant_message: content,
      artifacts: [],
      warnings: [],
      error:
        status === "failed"
          ? {
              code: "TIMEOUT",
              message: content,
              retryable: true,
            }
          : null,
      finished_at: "2026-07-08T14:28:42Z",
    },
    timestamp: 1_700_000_005,
    event_id: `terminal-${jobId}-1`,
  };
}

describe("585f367d — stage lifecycle in job-reducer", () => {
  it("stage_start sets job.stage", () => {
    const state = createJobState("job-1", "resource_generation");
    const next = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "intent_understanding",
      }),
    );
    expect(next.jobsById["job-1"].stage).toBe("intent_understanding");
  });

  it("nested stage_start leaves the inner stage as job.stage", () => {
    let state = createJobState("job-1", "resource_generation");
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "intent_understanding",
      }),
    );
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "knowledge_graph_query",
      }),
    );
    expect(state.jobsById["job-1"].stage).toBe("knowledge_graph_query");
  });

  it("stage_end pops the matching stage; job.stage falls back to outer", () => {
    let state = createJobState("job-1", "resource_generation");
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "intent_understanding",
      }),
    );
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "knowledge_graph_query",
      }),
    );
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_end",
        stage: "knowledge_graph_query",
      }),
    );
    expect(state.jobsById["job-1"].stage).toBe("intent_understanding");
  });

  it("stage_end pops the outer stage; job.stage becomes empty when stack drains", () => {
    let state = createJobState("job-1", "resource_generation");
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "intent_understanding",
      }),
    );
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_end",
        stage: "intent_understanding",
      }),
    );
    expect(state.jobsById["job-1"].stage).toBe("");
  });

  it("585f367d regression: trailing unmatched stage_start + job_terminal leaves stage=''", () => {
    // Sequence:
    // 1. parallel_resource_generation starts (parent)
    // 2. parallel_resource_generation ends
    // 3. video_rendering stage_start fires (note: per backend bug, this
    //    is missing in 585f367d but the test simulates the general
    //    "trailing unmatched start" pattern)
    // 4. job_terminal fires WITHOUT a stage_end for video_rendering
    //
    // Pre-fix: job.stage was frozen on "video_rendering" forever and
    // StageProgress showed it as active-spinner. Post-fix: the stack
    // is cleared on job_terminal, so job.stage is "".
    let state = createJobState("job-1", "resource_generation");
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "parallel_resource_generation",
      }),
    );
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_end",
        stage: "parallel_resource_generation",
      }),
    );
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "video_rendering",
      }),
    );
    // No stage_end for video_rendering.
    state = reduceJobEvent(
      state,
      terminalEvent("job-1", "failed", "Job timed out after 600s"),
    );
    expect(state.jobsById["job-1"].status).toBe("failed");
    expect(state.jobsById["job-1"].stage).toBe(
      "",
      "stage must be empty after terminal — no more 'active' spinner",
    );
  });

  it("stage_start after job_terminal does not reanimate the stage chip", () => {
    let state = createJobState("job-1", "resource_generation");
    state = reduceJobEvent(
      state,
      terminalEvent("job-1", "failed", "ok"),
    );
    state = reduceJobEvent(
      state,
      streamEvent("job-1", {
        type: "stage_start",
        stage: "parallel_resource_generation",
      }),
    );
    // We don't crash; we either ignore (status terminal) or just don't
    // reanimate the stage chip. Either way: stage must NOT become
    // "parallel_resource_generation" once the job is terminal.
    expect(state.jobsById["job-1"].status).toBe("failed");
    expect(state.jobsById["job-1"].stage).not.toBe(
      "parallel_resource_generation",
    );
  });
});