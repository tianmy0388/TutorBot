import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { Resource } from "@/lib/types";
import { VideoViewer } from "./VideoViewer";

const mocks = vi.hoisted(() => ({
  jobsById: {} as Record<string, unknown>,
  retryVideoRender: vi.fn(),
}));

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: unknown) => unknown) =>
    selector({ userId: "local-user", jobsById: mocks.jobsById }),
}));
vi.mock("@/lib/api", () => ({ retryVideoRender: mocks.retryVideoRender }));

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

function child(status: string) {
  return {
    parent: {
      children: [
        {
          job_id: `child-${status}`,
          parent_job_id: "parent",
          task_kind: "video_render",
          status,
          metadata: { package_id: "pkg-1", resource_id: "video-1" },
        },
      ],
    },
  };
}

beforeEach(() => {
  mocks.jobsById = {};
  mocks.retryVideoRender.mockReset();
  mocks.retryVideoRender.mockResolvedValue({
    job_id: "retry-child",
    status: "pending",
  });
});

afterEach(() => cleanup());

describe("VideoViewer durable render lifecycle", () => {
  it("renders a failed child after refresh instead of stale rendering state", () => {
    mocks.jobsById = child("failed");
    render(<VideoViewer resource={baseResource} />);

    expect(screen.getByText("渲染失败")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });

  it("renders a succeeded child as terminal even when the package snapshot is stale", () => {
    mocks.jobsById = child("succeeded");
    render(<VideoViewer resource={baseResource} />);

    expect(screen.getByText("渲染完成")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });

  it("uses terminal resource failure when child data is missing or stale", () => {
    mocks.jobsById = child("running");
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
    mocks.jobsById = {};
    render(<VideoViewer resource={baseResource} />);
    expect(screen.getByText("视频渲染中…")).toBeInTheDocument();
  });

  it("submits a new durable retry child without reopening the old child", async () => {
    mocks.jobsById = child("failed");
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
    expect(mocks.jobsById).toEqual(child("failed"));
  });

  it("surfaces retry request failure and leaves terminal failure visible", async () => {
    mocks.jobsById = child("failed");
    mocks.retryVideoRender.mockRejectedValueOnce(new Error("network down"));
    render(<VideoViewer resource={baseResource} />);

    fireEvent.click(screen.getByRole("button", { name: "重新渲染视频" }));

    expect(await screen.findByText("network down")).toBeInTheDocument();
    expect(screen.getByText("渲染失败")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });
});
