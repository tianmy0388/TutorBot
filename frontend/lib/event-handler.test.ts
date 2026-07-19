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
  messages: [] as Array<{ id: string; role: "assistant"; content: string; timestamp: number; metadata?: Record<string, unknown> }>,
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
  getResourcePackageDetail: vi.fn(),
}));

import { dispatchStreamEvent } from "./event-handler";
import { appendConversationMessage, getResourcePackageDetail } from "./api";
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
    ...RESOURCE_BASE,
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
        ...(type === "exercise"
          ? {
              format_specific: {
                questions: [
                  {
                    id: "q-valid",
                    type: "single_choice",
                    question: "有效练习题",
                    options: [{ label: "A", text: "有效选项" }],
                  },
                ],
              },
            }
          : {}),
        ...extras,
      },
      job_id: "job-bbf6ddbf",
    },
    seq: 1,
  };
}

function jobTerminalEvent(partialArtifacts: unknown[]): StreamEvent {
  return {
    ...RESOURCE_BASE,
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
    event_id: "evt-terminal",
  };
}

function inactiveStageEvent(
  type: "stage_start" | "stage_end",
  stage: string,
  eventId: string,
): StreamEvent {
  return {
    ...RESOURCE_BASE,
    type,
    source: "resource_capability",
    stage,
    content: "",
    metadata: {
      job_id: "job-bbf6ddbf",
      session_id: "session-a",
    },
    session_id: "session-a",
    seq: 1,
    event_id: eventId,
  };
}

function setCanonicalVideo(formatSpecific: Record<string, unknown>): void {
  mockStoreState.latestPackage = {
    package_id: "pkg-repair",
    resources: [
      {
        resource_id: "r-video",
        type: "video",
        title: "动画演示",
        content: "完整视频说明",
        metadata: { package_id: "pkg-repair" },
        format_specific: formatSpecific,
      },
    ],
  };
}

function dispatchVideoSnapshot(formatSpecific: Record<string, unknown>): void {
  dispatchStreamEvent(
    resourceEvent("r-video", "video", "动画演示", {
      metadata: { package_id: "pkg-repair" },
      format_specific: formatSpecific,
    }),
  );
}

function currentVideoFormat(): Record<string, unknown> {
  return (mockStoreState.latestPackage as {
    resources: Array<{ format_specific: Record<string, unknown> }>;
  }).resources[0].format_specific;
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
    vi.mocked(getResourcePackageDetail).mockReset();
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
    expect(finalPkg).not.toHaveProperty("summary");
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

  it("does not replace a canonical exercise with a truncated streamed copy", () => {
    mockStoreState.latestPackage = {
      package_id: "pkg-canonical",
      resources: [
        {
          resource_id: "r-exercise",
          type: "exercise",
          title: "练习题",
          content: "",
          format_specific: {
            questions: [
              { id: "q-1", options: [{ label: "A", text: "完整选项" }] },
            ],
          },
        },
      ],
    };

    const event = resourceEvent("r-exercise", "exercise", "练习题");
    event.type = "result";
    event.content = JSON.stringify({
      package: {
        package_id: "pkg-canonical",
        resources: [
          {
            ...((mockStoreState.latestPackage as {
              resources: unknown[];
            }).resources[0] as Record<string, unknown>),
            format_specific: {
              questions: [{ id: "q-1", options: ["[TRUNCATED]"] }],
            },
          },
        ],
      },
      summary: {},
    });

    dispatchStreamEvent(event);

    expect((mockStoreState.latestPackage as { resources: Array<{ format_specific: { questions: Array<{ options: unknown[] }> } }> }).resources[0].format_specific.questions[0].options)
      .toEqual([{ label: "A", text: "完整选项" }]);
  });

  it("does not add an invalid incremental resource to latestPackage", () => {
    dispatchStreamEvent(
      resourceEvent("r-truncated", "exercise", "练习题", {
        format_specific: {
          questions: [{ id: "q-1", options: ["[TRUNCATED]"] }],
        },
      }),
    );

    expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
    expect(mockStoreState.latestPackage).toBeNull();
  });

  it("hydrates a same-resource video repair snapshot after backend restart", () => {
    mockStoreState.latestPackage = {
      package_id: "pkg-repair",
      resources: [
        {
          resource_id: "r-video",
          type: "video",
          title: "动画演示",
          content: "",
          format_specific: {
            render_status: "failed",
            source_revision: 2,
            repair_status: "failed",
            repair_job_id: "repair-old",
          },
        },
      ],
    };

    dispatchStreamEvent(
      resourceEvent("r-video", "video", "动画演示", {
        metadata: { package_id: "pkg-repair" },
        format_specific: {
          render_status: "failed",
          source_revision: 2,
          repair_status: "running",
          repair_job_id: "repair-restarted",
          repair_history: [
            {
              job_id: "repair-old",
              failed_revision: 2,
              status: "failed",
              error_code: "repair_render_failed",
              summary: "上一轮安全诊断",
            },
          ],
        },
      }),
    );

    const updated = mockStoreState.setLatestPackage.mock.calls.at(-1)?.[0] as {
      resources: Array<{ format_specific: Record<string, unknown> }>;
    };
    expect(updated.resources).toHaveLength(1);
    expect(updated.resources[0].format_specific).toMatchObject({
      source_revision: 2,
      repair_status: "running",
      repair_job_id: "repair-restarted",
      repair_history: [expect.objectContaining({ summary: "上一轮安全诊断" })],
    });
  });

  it("accepts the first repair job at the same revision from a failed video without a repair job", () => {
    setCanonicalVideo({
      render_status: "failed",
      source_revision: 3,
      video_url: "/static/manim/last-good.mp4",
    });

    dispatchVideoSnapshot({
      render_status: "failed",
      source_revision: 3,
      repair_status: "pending",
      repair_job_id: "repair-first",
    });

    expect(mockStoreState.setLatestPackage).toHaveBeenCalledTimes(1);
    expect(currentVideoFormat()).toMatchObject({
      render_status: "failed",
      source_revision: 3,
      repair_status: "pending",
      repair_job_id: "repair-first",
      video_url: "/static/manim/last-good.mp4",
    });
  });

  it.each([
    ["failed", "pending"],
    ["ready", "running"],
  ])(
    "keeps same-job terminal repair %s over delayed %s",
    (terminalStatus, delayedStatus) => {
      setCanonicalVideo({
        render_status: terminalStatus === "ready" ? "ready" : "failed",
        source_revision: 3,
        repair_status: terminalStatus,
        repair_job_id: "repair-same",
        video_url: "/static/manim/current.mp4",
        repair_history: [
          {
            job_id: "repair-same",
            failed_revision: 3,
            status: terminalStatus,
            summary: "当前终态诊断",
          },
        ],
      });

      dispatchVideoSnapshot({
        render_status: "failed",
        source_revision: 3,
        repair_status: delayedStatus,
        repair_job_id: "repair-same",
        repair_history: [],
      });

      expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
      expect(currentVideoFormat()).toMatchObject({
        repair_status: terminalStatus,
        video_url: "/static/manim/current.mp4",
        repair_history: [expect.objectContaining({ summary: "当前终态诊断" })],
      });
    },
  );

  it("keeps the canonical ready URL over a conflicting same-terminal repair snapshot", () => {
    setCanonicalVideo({
      render_status: "ready",
      source_revision: 3,
      repair_status: "ready",
      repair_job_id: "repair-same",
      video_url: "/static/manim/canonical.mp4",
    });

    dispatchVideoSnapshot({
      render_status: "ready",
      source_revision: 3,
      repair_status: "ready",
      repair_job_id: "repair-same",
      video_url: "/static/manim/delayed.mp4",
    });

    expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
    expect(currentVideoFormat()).toMatchObject({
      repair_status: "ready",
      video_url: "/static/manim/canonical.mp4",
    });
  });

  it("keeps canonical failed history over a conflicting same-terminal repair snapshot", () => {
    setCanonicalVideo({
      render_status: "failed",
      source_revision: 3,
      repair_status: "failed",
      repair_job_id: "repair-same",
      repair_history: [
        {
          job_id: "repair-same",
          failed_revision: 3,
          status: "failed",
          summary: "canonical diagnosis",
        },
      ],
    });

    dispatchVideoSnapshot({
      render_status: "failed",
      source_revision: 3,
      repair_status: "failed",
      repair_job_id: "repair-same",
      repair_history: [
        {
          job_id: "repair-same",
          failed_revision: 3,
          status: "failed",
          summary: "delayed diagnosis",
        },
      ],
    });

    expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
    expect(currentVideoFormat()).toMatchObject({
      repair_status: "failed",
      repair_history: [expect.objectContaining({ summary: "canonical diagnosis" })],
    });
  });

  it("rejects a delayed old repair job when current history terminalizes it", () => {
    setCanonicalVideo({
      render_status: "failed",
      source_revision: 3,
      repair_status: "pending",
      repair_job_id: "repair-new",
      video_url: "/static/manim/last-good.mp4",
      repair_history: [
        {
          job_id: "repair-old",
          failed_revision: 3,
          status: "failed",
          summary: "旧任务已结束",
        },
      ],
    });

    dispatchVideoSnapshot({
      render_status: "failed",
      source_revision: 3,
      repair_status: "running",
      repair_job_id: "repair-old",
      repair_history: [],
    });

    expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
    expect(currentVideoFormat()).toMatchObject({
      repair_job_id: "repair-new",
      video_url: "/static/manim/last-good.mp4",
    });
  });

  it("accepts a causally newer repair attempt and preserves canonical history and video", () => {
    const oldHistory = {
      job_id: "repair-old",
      failed_revision: 3,
      status: "failed",
      summary: "旧任务已结束",
    };
    setCanonicalVideo({
      render_status: "failed",
      source_revision: 3,
      repair_status: "failed",
      repair_job_id: "repair-old",
      video_url: "/static/manim/last-good.mp4",
      repair_history: [oldHistory],
    });

    dispatchVideoSnapshot({
      render_status: "failed",
      source_revision: 3,
      repair_status: "pending",
      repair_job_id: "repair-new",
      repair_history: [oldHistory],
    });

    expect(mockStoreState.setLatestPackage).toHaveBeenCalledTimes(1);
    expect(currentVideoFormat()).toMatchObject({
      repair_status: "pending",
      repair_job_id: "repair-new",
      video_url: "/static/manim/last-good.mp4",
      repair_history: [expect.objectContaining({ job_id: "repair-old" })],
    });
  });

  it.each([
    ["failed", "pending"],
    ["ready", "rendering"],
  ])(
    "keeps legacy render terminal %s over delayed %s for the same job",
    (terminalStatus, delayedStatus) => {
      setCanonicalVideo({
        render_status: terminalStatus,
        render_job_id: "render-same",
        source_revision: 0,
        video_url: "/static/manim/legacy-current.mp4",
      });

      dispatchVideoSnapshot({
        render_status: delayedStatus,
        render_job_id: "render-same",
        source_revision: 0,
      });

      expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
      expect(currentVideoFormat()).toMatchObject({
        render_status: terminalStatus,
        video_url: "/static/manim/legacy-current.mp4",
      });
    },
  );

  it("recovers an invalid durable package once using the authoritative user", async () => {
    let resolveRecovery!: (value: unknown) => void;
    const recovery = new Promise<unknown>((resolve) => {
      resolveRecovery = resolve;
    });
    vi.mocked(getResourcePackageDetail).mockReturnValue(recovery as never);
    const event = resourceEvent("r-truncated", "exercise", "练习题", {
      format_specific: {
        questions: [{ id: "q-1", options: ["[TRUNCATED]"] }],
      },
      metadata: { package_id: "pkg-durable" },
    });

    dispatchStreamEvent(event, { userId: "authoritative-user" });
    dispatchStreamEvent(event, { userId: "authoritative-user" });

    await vi.waitFor(() => expect(getResourcePackageDetail).toHaveBeenCalledTimes(1));
    expect(getResourcePackageDetail).toHaveBeenCalledWith(
      "authoritative-user",
      "pkg-durable",
    );

    resolveRecovery({
      package_id: "pkg-durable",
      resources: [
        {
          resource_id: "r-document",
          type: "document",
          title: "已恢复资源",
          content: "完整内容",
        },
      ],
    });
    await vi.waitFor(() => expect(mockStoreState.setLatestPackage).toHaveBeenCalledTimes(1));
  });

  it("does not collide recovery dedupe keys across distinct user/package tuples", async () => {
    let resolveRecovery!: (value: unknown) => void;
    const recovery = new Promise<unknown>((resolve) => {
      resolveRecovery = resolve;
    });
    vi.mocked(getResourcePackageDetail).mockReturnValue(recovery as never);
    const invalidEvent = (packageId: string) =>
      resourceEvent("r-truncated", "exercise", "练习题", {
        format_specific: {
          questions: [{ id: "q-1", options: ["[TRUNCATED]"] }],
        },
        metadata: { package_id: packageId },
      });

    dispatchStreamEvent(invalidEvent("c"), { userId: "a:b" });
    dispatchStreamEvent(invalidEvent("c"), { userId: "a:b" });
    dispatchStreamEvent(invalidEvent("b:c"), { userId: "a" });
    dispatchStreamEvent(invalidEvent("b:c"), { userId: "a" });

    await vi.waitFor(() => expect(getResourcePackageDetail).toHaveBeenCalledTimes(2));
    expect(getResourcePackageDetail).toHaveBeenCalledWith("a:b", "c");
    expect(getResourcePackageDetail).toHaveBeenCalledWith("a", "b:c");

    resolveRecovery(undefined);
  });

  it("allows recovery to retry after a synchronous request failure", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.mocked(getResourcePackageDetail)
      .mockImplementationOnce(() => {
        throw new Error("adapter failure");
      })
      .mockResolvedValueOnce({
        package_id: "pkg-retry",
        resources: [
          {
            resource_id: "r-document",
            type: "document",
            title: "已恢复资源",
            content: "完整内容",
          },
        ],
      } as never);
    const event = resourceEvent("r-truncated", "exercise", "练习题", {
      format_specific: {
        questions: [{ id: "q-1", options: ["[TRUNCATED]"] }],
      },
      metadata: { package_id: "pkg-retry" },
    });

    dispatchStreamEvent(event, { userId: "authoritative-user" });
    await vi.waitFor(() => expect(warnSpy).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    dispatchStreamEvent(event, { userId: "authoritative-user" });

    await vi.waitFor(() => expect(getResourcePackageDetail).toHaveBeenCalledTimes(2));
    warnSpy.mockRestore();
  });

  it("does not let a stale recovery overwrite a newer canonical package", async () => {
    let resolveRecovery!: (value: unknown) => void;
    vi.mocked(getResourcePackageDetail).mockReturnValue(
      new Promise<unknown>((resolve) => {
        resolveRecovery = resolve;
      }) as never,
    );
    const invalid = resourceEvent("r-truncated", "exercise", "练习题", {
      format_specific: { questions: [{ id: "q-1", options: ["[TRUNCATED]"] }] },
      metadata: { package_id: "pkg-old" },
    });
    dispatchStreamEvent(invalid, { userId: "authoritative-user" });
    await vi.waitFor(() => expect(getResourcePackageDetail).toHaveBeenCalledTimes(1));

    const newer = resourceEvent("r-new", "document", "新资源");
    newer.type = "result";
    newer.content = JSON.stringify({
      package: { package_id: "pkg-new", resources: [newer.metadata.resource] },
      summary: {},
    });
    dispatchStreamEvent(newer);

    resolveRecovery({
      package_id: "pkg-old",
      resources: [{ resource_id: "r-old", type: "document", title: "旧资源", content: "完整内容" }],
    });
    await Promise.resolve();
    await Promise.resolve();

    expect((mockStoreState.latestPackage as { package_id: string }).package_id).toBe("pkg-new");
  });

  it("does not write a mismatched package recovered for an invalid stream", async () => {
    vi.mocked(getResourcePackageDetail).mockResolvedValueOnce({
      package_id: "pkg-other",
      resources: [{ resource_id: "r-other", type: "document", title: "其他资源", content: "完整内容" }],
    } as never);
    dispatchStreamEvent(
      resourceEvent("r-truncated", "exercise", "练习题", {
        format_specific: { questions: [{ id: "q-1", options: ["[TRUNCATED]"] }] },
        metadata: { package_id: "pkg-requested" },
      }),
      { userId: "authoritative-user" },
    );

    await vi.waitFor(() => expect(getResourcePackageDetail).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    expect(mockStoreState.latestPackage).toBeNull();
  });

  it("does not let a late partial terminal package replace a canonical package", () => {
    mockStoreState.latestPackage = {
      package_id: "pkg-canonical",
      resources: [{ resource_id: "r-canonical", type: "document", title: "完整资源", content: "完整内容" }],
    };

    dispatchStreamEvent(jobTerminalEvent([
      { resource_id: "r-partial", resource_type: "document", title: "部分资源" },
    ]));

    expect((mockStoreState.latestPackage as { package_id: string }).package_id).toBe("pkg-canonical");
  });

  it("rejects malformed partial artifacts and schedules durable recovery", async () => {
    const event = jobTerminalEvent([{ resource_id: "", title: "坏资源" }]);
    event.metadata = {
      ...event.metadata,
      contract: {
        ...(event.metadata.contract as Record<string, unknown>),
        package_id: "pkg-partial",
      },
    };

    dispatchStreamEvent(event, { userId: "authoritative-user" });

    await vi.waitFor(() => expect(getResourcePackageDetail).toHaveBeenCalledWith(
      "authoritative-user",
      "pkg-partial",
    ));
    expect(mockStoreState.setLatestPackage).not.toHaveBeenCalled();
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
    const persist = vi.fn().mockResolvedValue(undefined);
    const event = jobTerminalEvent([]);
    event.session_id = "";
    event.metadata = {
      ...event.metadata,
      session_id: undefined,
    };

    dispatchStreamEvent(event, {
      sessionId: "session-a",
      userId: "u-test",
      appendConversationMessage: persist,
    });
    await vi.waitFor(() =>
      expect(persist).toHaveBeenCalledTimes(2),
    );

    expect(persist).toHaveBeenCalledWith(
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
    expect(persist).toHaveBeenCalledWith(
      "u-test",
      "session-a",
      expect.objectContaining({
        content: "",
        metadata: expect.objectContaining({
          kind: "workflow_timeline",
          client_message_id: "workflow:job-bbf6ddbf",
        }),
      }),
    );
  });

  it("persists inactive terminal workflow stages from the real stream history", async () => {
    mockStoreState.sessionId = "session-b";
    const persist = vi.fn().mockResolvedValue(undefined);
    const context = {
      sessionId: "session-history-a",
      userId: "u-test",
      appendConversationMessage: persist,
    };

    dispatchStreamEvent(inactiveStageEvent("stage_start", "plan", "plan-start"), context);
    dispatchStreamEvent(inactiveStageEvent("stage_end", "plan", "plan-end"), context);
    dispatchStreamEvent(inactiveStageEvent("stage_start", "execute", "execute-start"), context);
    const terminal = jobTerminalEvent([]);
    terminal.session_id = "session-a";
    terminal.metadata = { ...terminal.metadata, session_id: "session-a" };
    dispatchStreamEvent(terminal, context);

    await vi.waitFor(() => expect(persist).toHaveBeenCalledTimes(2));
    const workflow = persist.mock.calls.find(
      ([, , message]) => message.metadata?.kind === "workflow_timeline",
    )?.[2];
    expect(workflow?.metadata?.workflow).toEqual({
      status: "failed",
      stages: [
        { name: "plan", status: "completed" },
        { name: "execute", status: "incomplete" },
      ],
    });
  });

  it("persists a rich inactive workflow only once across duplicate terminal replay", async () => {
    mockStoreState.sessionId = "session-b";
    const persist = vi.fn().mockResolvedValue(undefined);
    const context = {
      sessionId: "session-replay-a",
      userId: "u-test",
      appendConversationMessage: persist,
    };
    const stage = (type: "stage_start" | "stage_end", name: string, id: string) => ({
      ...inactiveStageEvent(type, name, id),
      session_id: "session-replay-a",
      metadata: { job_id: "job-bbf6ddbf", session_id: "session-replay-a" },
    });
    dispatchStreamEvent(stage("stage_start", "plan", "replay-plan-start"), context);
    dispatchStreamEvent(stage("stage_end", "plan", "replay-plan-end"), context);
    dispatchStreamEvent(stage("stage_start", "execute", "replay-execute-start"), context);
    const terminal = jobTerminalEvent([]);
    terminal.session_id = "session-replay-a";
    terminal.metadata = { ...terminal.metadata, session_id: "session-replay-a" };
    dispatchStreamEvent(terminal, context);
    dispatchStreamEvent(terminal, context);

    await vi.waitFor(() => expect(persist).toHaveBeenCalledTimes(2));
    expect(persist.mock.calls.filter(
      ([, , message]) => message.metadata?.kind === "workflow_timeline",
    )).toHaveLength(1);
    expect(persist.mock.calls.find(
      ([, , message]) => message.metadata?.kind === "workflow_timeline",
    )?.[2].metadata?.workflow).toEqual({
      status: "failed",
      stages: [
        { name: "plan", status: "completed" },
        { name: "execute", status: "incomplete" },
      ],
    });
  });

  it("retries only a failed inactive append after replay without parallel duplicates", async () => {
    mockStoreState.sessionId = "session-b";
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    let resolveAssistant!: () => void;
    let rejectWorkflow!: (error: Error) => void;
    const assistantPending = new Promise<void>((resolve) => {
      resolveAssistant = resolve;
    });
    const workflowPending = new Promise<void>((_, reject) => {
      rejectWorkflow = reject;
    });
    const persist = vi.fn()
      .mockImplementationOnce(() => assistantPending)
      .mockImplementationOnce(() => workflowPending)
      .mockResolvedValueOnce(undefined);
    const context = {
      sessionId: "session-retry-a",
      userId: "u-test",
      appendConversationMessage: persist,
    };
    const stage = (type: "stage_start" | "stage_end", name: string, id: string) => ({
      ...inactiveStageEvent(type, name, id),
      session_id: "session-retry-a",
      metadata: { job_id: "job-bbf6ddbf", session_id: "session-retry-a" },
    });
    dispatchStreamEvent(stage("stage_start", "plan", "retry-plan-start"), context);
    dispatchStreamEvent(stage("stage_end", "plan", "retry-plan-end"), context);
    dispatchStreamEvent(stage("stage_start", "execute", "retry-execute-start"), context);
    const terminal = jobTerminalEvent([]);
    terminal.session_id = "session-retry-a";
    terminal.metadata = { ...terminal.metadata, session_id: "session-retry-a" };

    dispatchStreamEvent(terminal, context);
    dispatchStreamEvent(terminal, context);
    await vi.waitFor(() => expect(persist).toHaveBeenCalledTimes(2));

    resolveAssistant();
    rejectWorkflow(new Error("workflow append failed"));
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    dispatchStreamEvent(terminal, context);

    await vi.waitFor(() => expect(persist).toHaveBeenCalledTimes(3));
    const assistantCalls = persist.mock.calls.filter(
      ([, , message]) => message.metadata?.client_message_id === "terminal:job-bbf6ddbf",
    );
    const workflowCalls = persist.mock.calls.filter(
      ([, , message]) => message.metadata?.kind === "workflow_timeline",
    );
    expect(assistantCalls).toHaveLength(1);
    expect(workflowCalls).toHaveLength(2);
    expect(workflowCalls[1][2]).toEqual(workflowCalls[0][2]);
    expect(workflowCalls[1][2].metadata?.workflow).toEqual({
      status: "failed",
      stages: [
        { name: "plan", status: "completed" },
        { name: "execute", status: "incomplete" },
      ],
    });
    expect(warnSpy).toHaveBeenCalledWith(
      "appendConversationMessage(workflow_timeline) failed",
      expect.any(Error),
    );
    warnSpy.mockRestore();
  });

  it("uses the injected persistence adapter without falling back to fetch", async () => {
    const persist = vi.fn().mockResolvedValue(undefined);
    const fetchSpy = vi.fn(() => {
      throw new Error("unexpected HTTP request");
    });
    vi.stubGlobal("fetch", fetchSpy);

    dispatchStreamEvent(jobTerminalEvent([]), {
      sessionId: "s-test",
      userId: "u-test",
      appendConversationMessage: persist,
    });

    await vi.waitFor(() => expect(persist).toHaveBeenCalledTimes(1));
    expect(appendConversationMessage).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("surfaces a structured error when nullable event content is absent", () => {
    dispatchStreamEvent({
      type: "error",
      source: "retrieval",
      stage: "retrieve",
      content: undefined,
      metadata: {
        job_id: "job-invalid-scope",
        error: {
          code: "INVALID_SCOPE",
          message: "请选择检索范围",
          details: { kind: null },
        },
      },
      session_id: "s-test",
    });

    expect(mockStoreState.addMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        content: "错误 [INVALID_SCOPE]: 请选择检索范围",
        metadata: expect.objectContaining({
          error: {
            code: "INVALID_SCOPE",
            message: "请选择检索范围",
            details: { kind: null },
          },
        }),
      }),
    );
  });

  it("fails closed at the parser boundary for an unknown public event type", () => {
    const publicPayload: unknown = {
      type: "future_event",
      metadata: { job_id: "job-future" },
      session_id: "s-test",
    };

    dispatchStreamEvent(publicPayload);

    expect(mockStoreState.applyStreamEvent).not.toHaveBeenCalled();
    expect(mockStoreState.addMessage).not.toHaveBeenCalled();
  });
});
