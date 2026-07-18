import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listJobs: vi.fn(),
  getJobStats: vi.fn(),
  getJobDetail: vi.fn(),
  cancelJob: vi.fn(),
  deleteJob: vi.fn(),
}));
const wsMocks = vi.hoisted(() => ({
  startJobMessage: vi.fn((input) => ({ type: "submit_job", ...input })),
  deferOpen: false,
  openCallbacks: [] as Array<() => void>,
}));

vi.mock("@/lib/api", () => apiMocks);
vi.mock("@/lib/ws", () => ({
  WsClient: class {
    options: any;
    constructor(options: any) {
      this.options = options;
    }
    connect() {
      if (wsMocks.deferOpen) {
        wsMocks.openCallbacks.push(() => this.options.onOpen());
      } else {
        this.options.onOpen();
      }
    }
    send(message: any) {
      if (message.type === "submit_job") {
        this.options.onEvent({
          type: "job_submitted",
          job_id: "job-web-search",
          capability: message.capability || "tutoring",
          status: "pending",
          created_at: "2026-07-18T00:00:00Z",
          session_id: message.sessionId,
        });
      }
    }
    close() {}
  },
  startJobMessage: wsMocks.startJobMessage,
}));

import { VideoViewer } from "@/components/resources/VideoViewer";
import { useJobQueue } from "@/hooks/useJobQueue";
import { useTutorStore } from "@/lib/store";
import type { JobStatus, Resource } from "@/lib/types";

const resource = {
  resource_id: "video-live",
  type: "video",
  title: "实时视频",
  content: "",
  format_specific: { render_status: "pending" },
  difficulty: 2,
  estimated_minutes: 5,
  prerequisites: [],
  generated_by: [],
  confidence_score: 0.8,
  topic: "并发",
  tags: [],
  created_at: "2026-07-17T00:00:00Z",
  metadata: { package_id: "pkg-live" },
} satisfies Resource;

function job(status: JobStatus) {
  return {
    job_id: "parent-live",
    user_id: "local-user",
    session_id: "current-page",
    capability: "resource_generation",
    status: "succeeded" as const,
    message_preview: "video",
    language: "zh",
    event_count: 1,
    created_at: "2026-07-17T00:00:00Z",
    started_at: "2026-07-17T00:00:01Z",
    finished_at: "2026-07-17T00:00:02Z",
    duration_seconds: 1,
    has_result: true,
    error: null,
    background_status: status,
    children: [
      {
        job_id: "child-live",
        capability: "video_render",
        parent_job_id: "parent-live",
        task_kind: "video_render",
        dedupe_key: "video:pkg-live:video-live",
        status,
        metadata: { package_id: "pkg-live", resource_id: "video-live" },
      },
    ],
  };
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  vi.useFakeTimers();
  apiMocks.getJobStats.mockResolvedValue(null);
  wsMocks.deferOpen = false;
  wsMocks.openCallbacks.length = 0;
  useTutorStore.setState({
    jobsById: {},
    jobOrder: [],
    messages: [],
    sessionId: "current-page",
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("useJobQueue durable child refresh", () => {
  it("submits the per-turn web-search choice only as observable metadata", async () => {
    apiMocks.listJobs.mockResolvedValue({ items: [], total: 0 });
    useTutorStore.setState({
      sessionId: "current-page",
      webSearchEnabled: true,
    });
    const { result } = renderHook(() => useJobQueue("local-user"));
    await act(flushPromises);

    await act(async () => {
      await result.current.submit("current question", "tutoring");
    });

    expect(wsMocks.startJobMessage).toHaveBeenCalledTimes(1);
    const envelope = wsMocks.startJobMessage.mock.calls[0][0];
    expect(envelope.sessionId).toBe("current-page");
    expect(envelope.metadata.web_search_requested).toBe(true);
    expect(envelope).not.toHaveProperty("web_search_enabled");
  });

  it("keeps the submitted session and web-search snapshot if navigation wins the WS open race", async () => {
    apiMocks.listJobs.mockResolvedValue({ items: [], total: 0 });
    wsMocks.deferOpen = true;
    useTutorStore.setState({
      sessionId: "session-a",
      webSearchEnabled: true,
    });
    const { result } = renderHook(() => useJobQueue("local-user"));
    await act(flushPromises);

    let submitted!: Promise<unknown>;
    act(() => {
      submitted = result.current.submit("question for A", "tutoring", {
        sessionId: "session-a",
        webSearchRequested: true,
      });
    });
    useTutorStore.setState({
      sessionId: "session-b",
      webSearchEnabled: false,
    });
    await act(async () => {
      wsMocks.openCallbacks.shift()?.();
      await submitted;
    });

    const envelope = wsMocks.startJobMessage.mock.calls[0][0];
    expect(envelope.sessionId).toBe("session-a");
    expect(envelope.metadata.session_id).toBe("session-a");
    expect(envelope.metadata.web_search_requested).toBe(true);
    expect(useTutorStore.getState().jobsById["job-web-search"]).toBeUndefined();
    expect(useTutorStore.getState().messages).toHaveLength(0);
  });

  it("serializes an explicit none retrieval scope without an invalid id suffix", async () => {
    apiMocks.listJobs.mockResolvedValue({ items: [], total: 0 });
    useTutorStore.setState({
      retrievalScope: { kind: "none" },
      ragEnabled: true,
    });
    const { result } = renderHook(() => useJobQueue("local-user"));
    await act(flushPromises);

    await act(async () => {
      await result.current.submit("question without a corpus", "tutoring");
    });

    const envelope = wsMocks.startJobMessage.mock.calls[0][0];
    expect(envelope.metadata.retrieval_scope).toBe("none");
  });

  it.each([
    ["failed" as const, "渲染失败"],
    ["succeeded" as const, "渲染完成"],
  ])(
    "hydrates a %s child on the 5s current-page poll without a page reload",
    async (terminalStatus, terminalText) => {
      useTutorStore.getState().applyReducerEvent({
        type: "snapshot",
        job: {
          job_id: "parent-live",
          capability: "resource_generation",
          status: "succeeded",
          message_preview: "video",
          submitted_at: Date.parse("2026-07-17T00:00:00Z"),
          started_at: null,
          finished_at: null,
          last_seq: 0,
          events: [],
          result: null,
          error: null,
          event_count: 0,
          background_status: "pending",
          children: job("pending").children,
        },
      });
      useTutorStore.getState().applyReducerEvent({
        type: "stream",
        job_id: "parent-live",
        event: {
          type: "progress",
          source: "resource_capability",
          stage: "video_rendering",
          content: "rendering",
          metadata: { job_id: "parent-live" },
          session_id: "current-page",
          turn_id: "",
          seq: 7,
          timestamp: Date.parse("2026-07-17T00:00:07Z") / 1000,
          event_id: "streamed-seven",
        },
      });
      apiMocks.listJobs
        .mockResolvedValueOnce({ items: [job("pending")], total: 1 })
        .mockResolvedValueOnce({ items: [job(terminalStatus)], total: 1 });

      renderHook(() => useJobQueue("local-user"));
      render(<VideoViewer resource={resource} />);
      await act(flushPromises);
      expect(screen.getByText("视频渲染中…")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
        await flushPromises();
      });

      expect(screen.getByText(terminalText)).toBeInTheDocument();
      expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
      expect(
        useTutorStore.getState().jobsById["parent-live"].children?.[0].status,
      ).toBe(terminalStatus);
      expect(useTutorStore.getState().jobsById["parent-live"].last_seq).toBe(7);
      expect(useTutorStore.getState().jobsById["parent-live"].events).toHaveLength(1);
    },
  );

  it("passes detail children and background status through store rehydration", () => {
    useTutorStore.getState().rehydrateJobFromDetail({
      ...job("failed"),
      events: [],
      result: null,
    });

    const hydrated = useTutorStore.getState().jobsById["parent-live"];
    expect(hydrated.children?.[0].status).toBe("failed");
    expect(hydrated.background_status).toBe("failed");
  });

  it("hydrates only the current session when another session has the same resource id", async () => {
    const other = {
      ...job("failed"),
      job_id: "parent-other",
      session_id: "other-page",
      children: [
        {
          ...job("failed").children[0],
          job_id: "child-other",
          parent_job_id: "parent-other",
        },
      ],
    };
    const current = job("pending");
    apiMocks.listJobs.mockResolvedValue({
      items: [other, current],
      total: 2,
    });

    const { result } = renderHook(() => useJobQueue("local-user"));
    render(<VideoViewer resource={resource} />);
    await act(flushPromises);

    expect(result.current.jobs).toHaveLength(2);
    expect(useTutorStore.getState().jobsById["parent-other"]).toBeUndefined();
    expect(useTutorStore.getState().jobsById["parent-live"]).toBeDefined();
    expect(screen.getByText("视频渲染中…")).toBeInTheDocument();
    expect(screen.queryByText("渲染失败")).not.toBeInTheDocument();
  });
});
