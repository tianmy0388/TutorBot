/**
 * Regression test for the bbf6ddbf trace.
 *
 * Sequence:
 *   1. The capability streams 4 ``RESOURCE`` events for the
 *      non-video resources. ``handleIncrementalResource`` already
 *      rendered them into ``latestPackage.resources`` with their
 *      FULL content payload.
 *   2. A single ``RESOURCE`` event fires for the VIDEO resource
 *      with ``render_status="pending"``.
 *   3. Job times out at 600s. ``job_terminal`` event arrives with
 *      ``contract.partial_artifacts`` mirroring the 5 ``RESOURCE``
 *      events.
 *   4. ``buildPartialPackageFromContract`` fires — and BUG: it
 *      blew away the 5 real resources from step 1-2 and replaced
 *      them with placeholder stubs whose ``content`` reads
 *      ``"此资源在任务超时前未完整生成，点击查看详情"``.
 *
 * After the fix, the 5 real resources must remain. The partial
 * package fallback is only used to fill in resource_ids that the
 * incremental events never delivered.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Mock the Zustand store so we can inspect the dispatched state.
const mockStoreState = {
  latestPackage: null as unknown,
  setLatestPackage: vi.fn((pkg: unknown) => {
    mockStoreState.latestPackage = pkg;
  }),
  applyStreamEvent: vi.fn(),
  addMessage: vi.fn(),
  completeActiveTurn: vi.fn(),
  userId: "u-test",
  sessionId: "s-test",
};

vi.mock("./store", () => ({
  useTutorStore: {
    getState: () => mockStoreState,
    setState: (updater: (s: typeof mockStoreState) => unknown) => {
      updater(mockStoreState);
    },
  },
}));

vi.mock("./api", () => ({
  appendConversationMessage: vi.fn().mockResolvedValue(undefined),
}));

import { dispatchStreamEvent } from "./event-handler";
import { appendConversationMessage } from "./api";
import type { StreamEvent } from "./types";

const RESOURCE_BASE = {
  source: "resource_capability",
  stage: "parallel_resource_generation",
  session_id: "s-test",
  turn_id: "t-test",
  timestamp: 1700000000,
  event_id: "evt-1",
};

function resourceEvent(
  resourceId: string,
  type: string,
  title: string,
  extras: Record<string, unknown> = {},
): StreamEvent {
  return {
    type: "resource",
    content: "",
    metadata: {
      resource_id: resourceId,
      resource_type: type,
      title,
      resource: {
        resource_id: resourceId,
        type,
        title,
        content: `<real content for ${title}>`,
        topic: "反向传播",
        difficulty: 3,
        estimated_minutes: 5,
        prerequisites: [],
        generated_by: ["test_agent"],
        confidence_score: 0.9,
        tags: [],
        ...extras,
      },
      job_id: "job-bbf6ddbf",
    },
    seq: 1,
    ...RESOURCE_BASE,
  };
}

function jobTerminalEvent(partialArtifacts: unknown[]): StreamEvent {
  return {
    type: "job_terminal",
    source: "job_runner",
    stage: "terminal",
    content: "任务失败：Job timed out after 600s (TUTOR_JOB_TIMEOUT_SECONDS)",
    metadata: {
      job_id: "job-bbf6ddbf",
      session_id: "s-test",
      contract: {
        job_id: "job-bbf6ddbf",
        capability: "resource_generation",
        status: "failed",
        assistant_message: "任务失败：Job timed out after 600s",
        error: {
          code: "TIMEOUT",
          message: "Job timed out after 600s",
          diagnostic: "Job timed out after 600s (TUTOR_JOB_TIMEOUT_SECONDS)",
          retryable: true,
        },
        finished_at: "2026-07-08T13:52:28Z",
        partial_artifacts: partialArtifacts,
      },
    },
    seq: 100,
    ...RESOURCE_BASE,
    event_id: "evt-terminal",
  };
}

describe("bbf6ddbf — buildPartialPackageFromContract must preserve real RESOURCE content", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ ok: true }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      ),
    );
    cleanup();
    mockStoreState.latestPackage = null;
    mockStoreState.setLatestPackage.mockClear();
    mockStoreState.applyStreamEvent.mockClear();
    mockStoreState.addMessage.mockClear();
    mockStoreState.completeActiveTurn.mockClear();
    vi.mocked(appendConversationMessage).mockClear();
    mockStoreState.sessionId = "s-test";
  });

  it("preserves 4 real non-video resources after job_terminal timeout", () => {
    // Step 1-2: capability streams 4 real non-video resources + 1 video(pending).
    for (const r of [
      resourceEvent("r-concept", "document", "概念讲解"),
      resourceEvent("r-explain", "document", "公式推导"),
      resourceEvent("r-exercise", "exercise", "练习题"),
      resourceEvent("r-summary", "reading", "总结"),
      resourceEvent(
        "r-video",
        "video",
        "动画演示",
        { format_specific: { render_status: "pending", manim_code: "..." } },
      ),
    ]) {
      dispatchStreamEvent(r);
    }

    // Sanity: 5 real resources are in latestPackage.
    const realPkg = mockStoreState.setLatestPackage.mock.calls.at(-1)?.[0] as {
      resources: Array<{ resource_id: string; content: string }>;
    };
    expect(realPkg.resources).toHaveLength(5);
    expect(realPkg.resources.find((r) => r.resource_id === "r-concept")?.content).toContain(
      "<real content for 概念讲解>",
    );

    // Step 3: job times out with partial_artifacts mirroring the 5 events.
    dispatchStreamEvent(
      jobTerminalEvent([
        { resource_type: "document", status: "succeeded", resource_id: "r-concept", title: "概念讲解" },
        { resource_type: "document", status: "succeeded", resource_id: "r-explain", title: "公式推导" },
        { resource_type: "exercise", status: "succeeded", resource_id: "r-exercise", title: "练习题" },
        { resource_type: "reading", status: "succeeded", resource_id: "r-summary", title: "总结" },
        { resource_type: "video", status: "succeeded", resource_id: "r-video", title: "动画演示" },
      ]),
    );

    // The terminal call should NOT have wiped the real resources.
    const finalPkg = mockStoreState.setLatestPackage.mock.calls.at(-1)?.[0] as {
      resources: Array<{ resource_id: string; content: string }>;
    };
    expect(finalPkg.resources).toHaveLength(5);
    for (const id of ["r-concept", "r-explain", "r-exercise", "r-summary", "r-video"]) {
      const r = finalPkg.resources.find((x) => x.resource_id === id);
      expect(r, `resource ${id} must still exist after job_terminal`).toBeDefined();
      // The bug: content was overwritten to the placeholder string.
      expect(r!.content, `resource ${id} content must NOT be the placeholder`).not.toContain(
        "此资源在任务超时前未完整生成",
      );
      expect(r!.content, `resource ${id} must keep its real content`).toContain(
        "<real content for",
      );
    }
  });

  it("fills placeholders for resource_ids only in partial_artifacts (not in incremental events)", () => {
    // Step 1-2: only 2 real resources streamed before timeout.
    dispatchStreamEvent(resourceEvent("r-concept", "document", "概念讲解"));
    dispatchStreamEvent(resourceEvent("r-explain", "document", "公式推导"));

    // Step 3: partial_artifacts lists 5 resource_ids (3 of which never streamed).
    dispatchStreamEvent(
      jobTerminalEvent([
        { resource_type: "document", status: "succeeded", resource_id: "r-concept", title: "概念讲解" },
        { resource_type: "document", status: "succeeded", resource_id: "r-explain", title: "公式推导" },
        { resource_type: "exercise", status: "succeeded", resource_id: "r-exercise", title: "练习题" },
        { resource_type: "reading", status: "succeeded", resource_id: "r-summary", title: "总结" },
        { resource_type: "video", status: "succeeded", resource_id: "r-video", title: "动画演示" },
      ]),
    );

    const finalPkg = mockStoreState.setLatestPackage.mock.calls.at(-1)?.[0] as {
      resources: Array<{ resource_id: string; content: string }>;
    };
    expect(finalPkg.resources).toHaveLength(5);

    // Real resources kept their content.
    const concept = finalPkg.resources.find((r) => r.resource_id === "r-concept")!;
    expect(concept.content).toContain("<real content for 概念讲解>");
    const explain = finalPkg.resources.find((r) => r.resource_id === "r-explain")!;
    expect(explain.content).toContain("<real content for 公式推导>");

    // Missing resources have the placeholder.
    const exercise = finalPkg.resources.find((r) => r.resource_id === "r-exercise")!;
    expect(exercise.content).toContain("此资源在任务超时前未完整生成");
    const video = finalPkg.resources.find((r) => r.resource_id === "r-video")!;
    expect(video.content).toContain("此资源在任务超时前未完整生成");
  });

  it("falls back to all-placeholder when no incremental RESOURCE events fired", () => {
    // No incremental events at all; latestPackage is empty.
    // job_terminal arrives with partial_artifacts.
    dispatchStreamEvent(
      jobTerminalEvent([
        { resource_type: "document", status: "succeeded", resource_id: "r-1", title: "资源 1" },
        { resource_type: "video", status: "succeeded", resource_id: "r-2", title: "动画" },
      ]),
    );

    const finalPkg = mockStoreState.setLatestPackage.mock.calls.at(-1)?.[0] as {
      resources: Array<{ resource_id: string; content: string }>;
    };
    expect(finalPkg.resources).toHaveLength(2);
    // Placeholder content for both, since no incremental events.
    expect(finalPkg.resources[0].content).toContain("此资源在任务超时前未完整生成");
    expect(finalPkg.resources[1].content).toContain("此资源在任务超时前未完整生成");
  });

  it("039b4a70 — duplicates the same resource_id in partial_artifacts do not appear twice in final package", () => {
    // Sequence that produces the React dup-key bug:
    // 1. RESOURCE event fires for the video (manim_video inline emit)
    // 2. RESOURCE event fires AGAIN for the same video (resource_capability
    //    as_completed yield)
    // 3. job_terminal arrives with partial_artifacts that ALSO has
    //    the same video twice (backend dedups, but we test the frontend
    //    layer too as defense in depth).
    const videoA = resourceEvent(
      "r-video",
      "video",
      "动画演示",
      { format_specific: { render_status: "pending", manim_code: "..." } },
    );
    dispatchStreamEvent(videoA);
    dispatchStreamEvent(videoA); // exact same event arrives twice

    dispatchStreamEvent(
      jobTerminalEvent([
        { resource_type: "video", status: "succeeded", resource_id: "r-video", title: "动画演示" },
        { resource_type: "video", status: "succeeded", resource_id: "r-video", title: "动画演示" },
      ]),
    );

    const finalPkg = mockStoreState.setLatestPackage.mock.calls.at(-1)?.[0] as {
      resources: Array<{ resource_id: string }>;
    };
    const ids = finalPkg.resources.map((r) => r.resource_id);
    const videoCount = ids.filter((id) => id === "r-video").length;
    expect(videoCount).toBe(1);
  });

  it("does not route session A resource events into the active session B view", () => {
    mockStoreState.sessionId = "session-b";
    const event = resourceEvent("r-session-a", "document", "A 的资源");
    event.session_id = "";

    dispatchStreamEvent(event, {
      sessionId: "session-a",
      userId: "u-test",
    });

    expect(mockStoreState.applyStreamEvent).not.toHaveBeenCalled();
    expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
    expect(mockStoreState.addMessage).not.toHaveBeenCalled();
  });

  it("does not report a malformed session A event inside active session B", () => {
    mockStoreState.sessionId = "session-b";
    const event = {
      ...resourceEvent("r-session-a", "document", "A 的资源"),
      type: "progress",
      metadata: {},
      session_id: "",
    } as StreamEvent;

    dispatchStreamEvent(event, {
      sessionId: "session-a",
      userId: "u-test",
    });

    expect(mockStoreState.applyStreamEvent).not.toHaveBeenCalled();
    expect(mockStoreState.addMessage).not.toHaveBeenCalled();
  });

  it("fails closed when a terminal event has no authoritative session", async () => {
    mockStoreState.sessionId = "session-b";
    const event = jobTerminalEvent([]);
    event.session_id = "";
    event.metadata = {
      ...event.metadata,
      session_id: undefined,
    };

    dispatchStreamEvent(event);
    await Promise.resolve();

    expect(mockStoreState.applyStreamEvent).not.toHaveBeenCalled();
    expect(mockStoreState.completeActiveTurn).not.toHaveBeenCalled();
    expect(appendConversationMessage).not.toHaveBeenCalled();
  });

  it("persists session A terminal assistant to A without mutating active session B", async () => {
    mockStoreState.sessionId = "session-b";
    const event = jobTerminalEvent([]);
    event.session_id = "";
    event.metadata = {
      ...event.metadata,
      session_id: undefined,
    };

    dispatchStreamEvent(event, {
      sessionId: "session-a",
      userId: "u-test",
    });
    await vi.waitFor(() =>
      expect(appendConversationMessage).toHaveBeenCalledTimes(1),
    );

    expect(appendConversationMessage).toHaveBeenCalledWith(
      "u-test",
      "session-a",
      expect.objectContaining({
        role: "assistant",
        content: "任务失败：Job timed out after 600s",
        job_id: "job-bbf6ddbf",
      }),
    );
    expect(mockStoreState.applyStreamEvent).not.toHaveBeenCalled();
    expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
    expect(mockStoreState.addMessage).not.toHaveBeenCalled();
    expect(mockStoreState.completeActiveTurn).not.toHaveBeenCalled();
  });
});
