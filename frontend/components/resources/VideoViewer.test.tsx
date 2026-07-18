import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { useTutorStore } from "@/lib/store";
import type { JobChildSummary, Resource, ResourcePackage } from "@/lib/types";
import { VideoViewer } from "./VideoViewer";

const mocks = vi.hoisted(() => ({
  retryVideoRender: vi.fn(),
  getJobDetail: vi.fn(),
  getResourcePackageDetail: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  retryVideoRender: mocks.retryVideoRender,
  getJobDetail: mocks.getJobDetail,
  getResourcePackageDetail: mocks.getResourcePackageDetail,
}));

const baseResource = {
  resource_id: "video-1",
  type: "video",
  title: "反向传播",
  content: "",
  format_specific: { render_status: "pending" },
  difficulty: 2,
  estimated_minutes: 5,
  prerequisites: [],
  generated_by: [],
  confidence_score: 0.8,
  topic: "反向传播",
  tags: [],
  created_at: "2026-07-17T00:00:00Z",
  metadata: { package_id: "pkg-1" },
} satisfies Resource;

function childSummary(status: JobChildSummary["status"]): JobChildSummary {
  return {
    job_id: `child-${status}`,
    capability: "video_render",
    parent_job_id: "parent",
    task_kind: "video_render",
    status,
    metadata: { package_id: "pkg-1", resource_id: "video-1" },
  };
}

function setChildren(...children: JobChildSummary[]) {
  useTutorStore.getState().rehydrateJobFromDetail({
    job_id: "parent",
    capability: "resource_generation",
    status: "succeeded",
    message_preview: "",
    created_at: "2026-07-17T00:00:00Z",
    events: [],
    event_count: 0,
    children,
  });
}

function packageWith(resource: Resource): ResourcePackage {
  return {
    package_id: "pkg-1",
    topic: resource.topic,
    resources: [resource],
    target_profile_snapshot: {},
    learning_path_summary: {},
    generated_by: [],
    metadata: {},
    created_at: "2026-07-17T00:00:00Z",
  };
}

function setCanonicalResource(resource: Resource) {
  useTutorStore.getState().setLatestPackage(packageWith(resource));
}

beforeEach(() => {
  useTutorStore.setState({
    userId: "local-user",
    sessionId: "",
    jobsById: {},
    jobOrder: [],
    latestPackage: packageWith(baseResource),
  });
  mocks.retryVideoRender.mockReset();
  mocks.getJobDetail.mockReset();
  mocks.getResourcePackageDetail.mockReset();
  mocks.retryVideoRender.mockResolvedValue({
    job_id: "retry-child",
    parent_job_id: "parent",
    package_id: "pkg-1",
    resource_id: "video-1",
    status: "pending",
    child: {
      job_id: "retry-child",
      capability: "video_render",
      parent_job_id: "parent",
      task_kind: "video_render",
      status: "pending",
      metadata: { package_id: "pkg-1", resource_id: "video-1" },
    },
    resource: {
      ...baseResource,
      format_specific: {
        ...baseResource.format_specific,
        render_status: "pending",
        render_job_id: "retry-child",
      },
    },
  });
});

afterEach(() => cleanup());

describe("VideoViewer durable render lifecycle", () => {
  it("renders a failed child after refresh instead of stale rendering state", () => {
    setChildren(childSummary("failed"));
    render(<VideoViewer resource={baseResource} />);

    expect(screen.getByText("渲染失败")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });

  it("renders a succeeded child as terminal even when the package snapshot is stale", () => {
    setChildren(childSummary("succeeded"));
    render(<VideoViewer resource={baseResource} />);

    expect(screen.getByText("渲染完成")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });

  it("uses terminal resource failure when child data is missing or stale", () => {
    setChildren(childSummary("running"));
    const failed = {
      ...baseResource,
      format_specific: {
        render_status: "failed",
        render_failure: {
          error_code: "missing_external_asset",
          summary: "缺少动画资源文件",
          traceback_tail: ["trace 119", "FileNotFoundError: person.svg"],
          log_artifact_key: "manim_logs/child/attempt-01.log",
        },
        artifacts: [
          {
            name: "attempt-01.log",
            kind: "render_log",
            artifact_key: "manim_logs/child/attempt-01.log",
          },
        ],
      },
    } satisfies Resource;
    setCanonicalResource(failed);

    render(<VideoViewer resource={failed} />);

    expect(screen.getByText("缺少动画资源文件")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
    expect(screen.getByText("查看技术详情")).toBeInTheDocument();
    expect(screen.getByText(/FileNotFoundError/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看完整渲染日志" })).toHaveAttribute(
      "href",
      "/api/v1/resources/packages/local-user/pkg-1/resources/video-1/artifacts/attempt-01.log",
    );
  });

  it("shows a spinner only when a canonical resource or child is non-terminal", () => {
    render(<VideoViewer resource={baseResource} />);
    expect(screen.getByText("视频渲染中…")).toBeInTheDocument();
  });

  it("submits a new durable retry child without reopening the old child", async () => {
    setChildren(childSummary("failed"));
    render(<VideoViewer resource={baseResource} />);

    fireEvent.click(screen.getByRole("button", { name: "重新渲染视频" }));

    await waitFor(() =>
      expect(mocks.retryVideoRender).toHaveBeenCalledWith(
        "local-user",
        "pkg-1",
        "video-1",
      ),
    );
    expect(screen.getByText("重试任务已排队")).toBeInTheDocument();
    expect(
      useTutorStore.getState().jobsById.parent.children?.[0],
    ).toEqual(childSummary("failed"));
  });

  it("reconciles a new retry revision through running to a playable resource", async () => {
    vi.useFakeTimers();
    const failed = {
      ...baseResource,
      format_specific: {
        render_status: "failed",
        render_job_id: "child-failed",
        render_failure: {
          error_code: "process_exit",
          summary: "旧渲染失败",
          traceback_tail: ["ValueError: old failure"],
          log_artifact_key: "manim_logs/old/attempt.log",
        },
      },
    } satisfies Resource;
    setCanonicalResource(failed);
    setChildren(childSummary("failed"));

    let resolveRunning: (value: unknown) => void = () => {};
    mocks.getJobDetail
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveRunning = resolve;
        }),
      )
      .mockResolvedValueOnce({
        job_id: "parent",
        capability: "resource_generation",
        status: "succeeded",
        message_preview: "",
        created_at: "2026-07-17T00:00:00Z",
        events: [],
        event_count: 0,
        children: [
          childSummary("failed"),
          {
            ...childSummary("succeeded"),
            job_id: "retry-child",
          },
        ],
      });
    const ready = {
      ...failed,
      format_specific: {
        render_status: "ready",
        render_job_id: "retry-child",
        video_url: "/static/manim/retry.mp4",
        artifact_key: "manim_videos/retry.mp4",
      },
    } satisfies Resource;
    mocks.getResourcePackageDetail.mockResolvedValue(packageWith(ready));
    render(<VideoViewer resource={failed} />);

    fireEvent.click(screen.getByRole("button", { name: "重新渲染视频" }));

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText("旧渲染失败")).not.toBeInTheDocument();
    expect(screen.getByText("视频渲染中…")).toBeInTheDocument();
    expect(
      useTutorStore.getState().jobsById.parent.children?.map((item) => item.job_id),
    ).toEqual(["child-failed", "retry-child"]);

    await act(async () => {
      resolveRunning({
        job_id: "parent",
        capability: "resource_generation",
        status: "succeeded",
        message_preview: "",
        created_at: "2026-07-17T00:00:00Z",
        events: [],
        event_count: 0,
        children: [
          childSummary("failed"),
          {
            ...childSummary("running"),
            job_id: "retry-child",
          },
        ],
      });
      await Promise.resolve();
    });
    expect(
      useTutorStore.getState().jobsById.parent.children?.at(-1)?.status,
    ).toBe("running");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(mocks.getResourcePackageDetail).toHaveBeenCalledWith(
      "local-user",
      "pkg-1",
    );
    expect(document.querySelector("source")).toHaveAttribute(
      "src",
      "/static/manim/retry.mp4",
    );
    expect(screen.queryByText("渲染失败")).not.toBeInTheDocument();
    expect(
      useTutorStore.getState().jobsById.parent.children?.[0].status,
    ).toBe("failed");
    vi.useRealTimers();
  });

  it("surfaces retry request failure and leaves terminal failure visible", async () => {
    setChildren(childSummary("failed"));
    mocks.retryVideoRender.mockRejectedValueOnce(new Error("network down"));
    render(<VideoViewer resource={baseResource} />);

    fireEvent.click(screen.getByRole("button", { name: "重新渲染视频" }));

    expect(await screen.findByText("network down")).toBeInTheDocument();
    expect(screen.getByText("渲染失败")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });
});
