import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createJobState } from "@/lib/job-reducer";
import type { JobSummary } from "@/lib/types";

const useJobQueueMock = vi.fn();
const useTutorStoreMock = vi.fn();

vi.mock("@/hooks/useJobQueue", () => ({
  useJobQueue: (...args: unknown[]) => useJobQueueMock(...args),
}));
vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: unknown) => unknown) =>
    useTutorStoreMock(selector),
}));

import { JobTray } from "./JobTray";

function summary(status: JobSummary["status"]): JobSummary {
  return {
    job_id: "job-queue",
    user_id: "local-user",
    session_id: "session-1",
    capability: "tutoring",
    status,
    message_preview: "hello",
    language: "zh",
    event_count: 1,
    created_at: "2026-07-17T00:00:00Z",
    started_at: "2026-07-17T00:00:01Z",
    finished_at: null,
    duration_seconds: null,
    has_result: false,
    error: null,
  };
}

function mockQueue(job: JobSummary) {
  const queue = {
    jobs: [job],
    total: 1,
    loading: false,
    error: null,
    stats: null,
    activeJobs: job.status === "pending" || job.status === "running" ? [job] : [],
    refresh: vi.fn(),
    subscribe: vi.fn(),
    cancel: vi.fn(),
    remove: vi.fn(),
  };
  useJobQueueMock.mockReturnValue(queue);
  return queue;
}

describe("JobTray durable terminal state", () => {
  afterEach(() => {
    cleanup();
    useJobQueueMock.mockReset();
    useTutorStoreMock.mockReset();
  });

  it("shows the durable failed state instead of a stale pending queue row", () => {
    mockQueue(summary("pending"));
    const state = createJobState("job-queue", "tutoring");
    state.jobsById["job-queue"].status = "failed";
    state.jobsById["job-queue"].finished_at = Date.now();
    state.jobsById["job-queue"].error = {
      code: "CAPABILITY_FAILED",
      message: "能力执行失败",
    };
    useTutorStoreMock.mockImplementation((selector: (value: unknown) => unknown) =>
      selector({ userId: "local-user", jobsById: state.jobsById }),
    );

    render(<JobTray />);
    fireEvent.click(screen.getByTitle("任务记录"));

    expect(screen.queryByText("排队中")).not.toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("[CAPABILITY_FAILED] 能力执行失败")).toBeInTheDocument();
    expect(screen.queryByText("1 运行中")).not.toBeInTheDocument();
  });

  it("does not revive a terminal parent spinner for running child background work", () => {
    mockQueue(summary("succeeded"));
    const state = createJobState("job-queue", "resource_generation");
    const parent = state.jobsById["job-queue"];
    parent.status = "succeeded";
    parent.finished_at = Date.now();
    parent.background_status = "running";
    parent.children = [
      {
        job_id: "child-video",
        capability: "video_render",
        status: "running",
        parent_job_id: "job-queue",
        task_kind: "video_render",
      },
    ];
    useTutorStoreMock.mockImplementation((selector: (value: unknown) => unknown) =>
      selector({ userId: "local-user", jobsById: state.jobsById }),
    );

    render(<JobTray />);

    expect(screen.queryByText("1 运行中")).not.toBeInTheDocument();
  });

  it("counts a durable running job before the queue refresh includes it", () => {
    useJobQueueMock.mockReturnValue({
      jobs: [],
      total: 0,
      loading: false,
      error: null,
      stats: null,
      activeJobs: [],
      refresh: vi.fn(),
      subscribe: vi.fn(),
      cancel: vi.fn(),
      remove: vi.fn(),
    });
    const state = createJobState("job-durable-only", "tutoring");
    state.jobsById["job-durable-only"].status = "running";
    state.jobsById["job-durable-only"].message_preview = "hello";
    useTutorStoreMock.mockImplementation((selector: (value: unknown) => unknown) =>
      selector({ userId: "local-user", jobsById: state.jobsById }),
    );

    render(<JobTray />);

    expect(screen.getByTitle("任务记录")).toHaveTextContent("1");
    fireEvent.click(screen.getByTitle("任务记录"));
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("hello")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
  });

  it("renders one row when queue and durable state overlap", () => {
    mockQueue(summary("pending"));
    const state = createJobState("job-queue", "tutoring");
    state.jobsById["job-queue"].status = "running";
    state.jobsById["job-queue"].message_preview = "hello";
    useTutorStoreMock.mockImplementation((selector: (value: unknown) => unknown) =>
      selector({ userId: "local-user", jobsById: state.jobsById }),
    );

    render(<JobTray />);
    fireEvent.click(screen.getByTitle("任务记录"));

    expect(screen.getAllByText("hello")).toHaveLength(1);
  });

  it("resubscribes with the job's authoritative session", () => {
    const queue = mockQueue(summary("running"));
    useTutorStoreMock.mockImplementation((selector: (value: unknown) => unknown) =>
      selector({ userId: "local-user", jobsById: {} }),
    );

    render(<JobTray />);
    fireEvent.click(screen.getByTitle("任务记录"));
    fireEvent.click(screen.getByRole("button", { name: "查看" }));

    expect(queue.subscribe).toHaveBeenCalledWith("job-queue", "tutoring", {
      sessionId: "session-1",
    });
  });
});
