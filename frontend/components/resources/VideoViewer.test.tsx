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
import { createRetryPollingDelay, VideoViewer } from "./VideoViewer";

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

const failedResource = {
  ...baseResource,
  format_specific: {
    render_status: "failed",
    render_job_id: "initial-render-child",
    source_revision: 0,
    render_failure: {
      error_code: "process_exit",
      summary: "原始 Manim 渲染失败",
      traceback_tail: ["ValueError: original failure"],
      log_artifact_key: "manim_logs/initial/attempt.log",
    },
  },
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

function retryChild(status: JobChildSummary["status"]): JobChildSummary {
  return {
    ...childSummary(status),
    job_id: "retry-child",
    capability: "video_repair_render",
    task_kind: "video_repair_render",
    dedupe_key: "video-repair:pkg-1:video-1:0:1",
    metadata: {
      package_id: "pkg-1",
      resource_id: "video-1",
      failed_revision: 0,
    },
  };
}

function retryResponse(resource: Resource = failedResource) {
  return {
    job_id: "retry-child",
    parent_job_id: "parent",
    package_id: "pkg-1",
    resource_id: "video-1",
    status: "pending" as const,
    child: retryChild("pending"),
    resource: {
      ...resource,
      format_specific: {
        ...resource.format_specific,
        repair_status: "pending",
        repair_job_id: "retry-child",
      },
    },
  };
}

function parentDetail(status: JobChildSummary["status"]) {
  return {
    job_id: "parent",
    capability: "resource_generation",
    status: "succeeded" as const,
    message_preview: "",
    created_at: "2026-07-17T00:00:00Z",
    events: [],
    event_count: 0,
    children: [childSummary("failed"), retryChild(status)],
  };
}

const readyResource = {
  ...baseResource,
  format_specific: {
    render_status: "ready",
    repair_status: "ready",
    repair_job_id: "retry-child",
    source_revision: 1,
    video_url: "/static/manim/retry.mp4",
    artifact_key: "manim_videos/retry.mp4",
  },
} satisfies Resource;

async function beginRetry() {
  fireEvent.click(
    screen.getByRole("button", { name: "智能修复并重新渲染" }),
  );
  await act(async () => {
    await Promise.resolve();
  });
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
  mocks.retryVideoRender.mockResolvedValue(retryResponse());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

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

  it("never renders a legacy raw traceback as the failure summary", () => {
    const legacyFailure = {
      ...baseResource,
      format_specific: {
        render_status: "failed",
        render_error:
          "+--- Traceback (most recent call last) ---+ E:\\private\\scene.py",
        manim_code:
          'from manim import *\nclass MainScene(Scene):\n    pass\n',
      },
    } satisfies Resource;
    setCanonicalResource(legacyFailure);

    render(<VideoViewer resource={legacyFailure} />);

    expect(screen.getByText("渲染流程未生成可播放视频。")).toBeInTheDocument();
    expect(screen.queryByText(/Traceback \(most recent call last\)/)).not.toBeInTheDocument();
    expect(screen.queryByText(/E:\\private/)).not.toBeInTheDocument();
  });

  it("shows a spinner only when a canonical resource or child is non-terminal", () => {
    render(<VideoViewer resource={baseResource} />);
    expect(screen.getByText("视频渲染中…")).toBeInTheDocument();
  });

  it("labels the failed-video action as intelligent regeneration and preserves the original failure", async () => {
    setCanonicalResource(failedResource);
    setChildren(childSummary("failed"));
    render(<VideoViewer resource={failedResource} />);

    fireEvent.click(
      screen.getByRole("button", { name: "智能修复并重新渲染" }),
    );

    await waitFor(() =>
      expect(mocks.retryVideoRender).toHaveBeenCalledWith(
        "local-user",
        "pkg-1",
        "video-1",
      ),
    );
    expect(screen.getByText("原始 Manim 渲染失败")).toBeInTheDocument();
    expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
    expect(
      useTutorStore.getState().jobsById.parent.children?.[0],
    ).toEqual(childSummary("failed"));
  });

  it("keeps intelligent repair single-flight while its request is pending", async () => {
    setCanonicalResource(failedResource);
    let resolveRetry: (value: unknown) => void = () => {};
    mocks.retryVideoRender.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRetry = resolve;
      }),
    );
    render(<VideoViewer resource={failedResource} />);

    const action = screen.getByRole("button", {
      name: "智能修复并重新渲染",
    });
    fireEvent.click(action);
    fireEvent.click(action);

    expect(action).toBeDisabled();
    expect(mocks.retryVideoRender).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveRetry(undefined);
      await Promise.resolve();
    });
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
        repair_status: "ready",
        repair_job_id: "retry-child",
        source_revision: 1,
        video_url: "/static/manim/retry.mp4",
        artifact_key: "manim_videos/retry.mp4",
      },
    } satisfies Resource;
    mocks.getResourcePackageDetail.mockResolvedValue(packageWith(ready));
    mocks.retryVideoRender.mockResolvedValueOnce(retryResponse(failed));
    render(<VideoViewer resource={failed} />);

    fireEvent.click(
      screen.getByRole("button", { name: "智能修复并重新渲染" }),
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("旧渲染失败")).toBeInTheDocument();
    expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();
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
  });

  it("shows the latest bounded repair diagnostic and permits another manual repair", async () => {
    setCanonicalResource({
      ...failedResource,
      format_specific: {
        ...failedResource.format_specific,
        repair_status: "failed",
        repair_job_id: "repair-failed",
        repair_history: Array.from({ length: 12 }, (_, index) => ({
          job_id: `repair-${index}`,
          failed_revision: index,
          status: "failed",
          error_code: "repair_render_failed",
          summary: `安全诊断 ${index}`,
        })),
      },
    });

    render(<VideoViewer resource={failedResource} />);

    expect(screen.getByText("智能修复失败")).toBeInTheDocument();
    expect(screen.getByText("安全诊断 11")).toBeInTheDocument();
    expect(screen.queryByText("安全诊断 0")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "智能修复并重新渲染" }),
    ).toBeEnabled();
  });

  it("lets a canonical repair failure override a stale running child", () => {
    const terminalRepairFailure = {
      ...failedResource,
      format_specific: {
        ...failedResource.format_specific,
        repair_status: "failed",
        repair_job_id: "retry-child",
        repair_history: [
          {
            job_id: "retry-child",
            failed_revision: 0,
            status: "failed",
            error_code: "repair_render_failed",
            summary: "重启后恢复的安全诊断",
          },
        ],
      },
    } satisfies Resource;
    setCanonicalResource(terminalRepairFailure);
    setChildren(childSummary("failed"), retryChild("running"));

    render(<VideoViewer resource={terminalRepairFailure} />);

    expect(screen.getByText("智能修复失败")).toBeInTheDocument();
    expect(screen.getByText("重启后恢复的安全诊断")).toBeInTheDocument();
    expect(
      screen.queryByText("正在生成修复代码并重新渲染…"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "智能修复并重新渲染" }),
    ).toBeEnabled();
  });

  it("drops local retry tracking when the matching canonical repair fails", async () => {
    setCanonicalResource(failedResource);
    mocks.getJobDetail.mockReturnValue(new Promise(() => {}));
    render(<VideoViewer resource={failedResource} />);

    await beginRetry();
    expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();
    expect(mocks.getJobDetail).toHaveBeenCalledWith("local-user", "parent");

    const terminalFailure = {
      ...failedResource,
      format_specific: {
        ...failedResource.format_specific,
        repair_status: "failed",
        repair_job_id: "retry-child",
        repair_history: [
          {
            job_id: "retry-child",
            failed_revision: 0,
            status: "failed",
            error_code: "repair_render_failed",
            summary: "本轮修复已终止",
          },
        ],
      },
    } satisfies Resource;
    await act(async () => {
      setCanonicalResource(terminalFailure);
      await Promise.resolve();
    });

    expect(screen.queryByText("正在生成修复代码并重新渲染…")).not.toBeInTheDocument();
    expect(screen.getByText("智能修复失败")).toBeInTheDocument();
    expect(screen.getByText("本轮修复已终止")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "智能修复并重新渲染" }),
    ).toBeEnabled();
  });

  it("drops failed local polling when the matching canonical repair becomes ready", async () => {
    vi.useFakeTimers();
    setCanonicalResource(failedResource);
    mocks.getJobDetail.mockRejectedValueOnce(new Error("stale local poll"));
    render(<VideoViewer resource={failedResource} />);

    await beginRetry();
    expect(screen.getByText(/stale local poll/)).toBeInTheDocument();
    expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();

    await act(async () => {
      setCanonicalResource(readyResource);
      await Promise.resolve();
    });

    expect(screen.queryByText("正在生成修复代码并重新渲染…")).not.toBeInTheDocument();
    expect(screen.queryByText(/stale local poll/)).not.toBeInTheDocument();
    expect(document.querySelector("source")).toHaveAttribute(
      "src",
      "/static/manim/retry.mp4",
    );
    expect(
      screen.queryByRole("button", { name: "智能修复并重新渲染" }),
    ).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stops polling a stale local job when canonical repair identity changes", async () => {
    setCanonicalResource(failedResource);
    mocks.getJobDetail.mockReturnValue(new Promise(() => {}));
    render(<VideoViewer resource={failedResource} />);

    await beginRetry();
    expect(mocks.getJobDetail).toHaveBeenNthCalledWith(1, "local-user", "parent");

    const newRepairChild = {
      ...retryChild("running"),
      job_id: "repair-new",
      parent_job_id: "parent-new",
      dedupe_key: "video-repair:pkg-1:video-1:0:2",
    } satisfies JobChildSummary;
    const newCanonicalRepair = {
      ...failedResource,
      format_specific: {
        ...failedResource.format_specific,
        repair_status: "running",
        repair_job_id: "repair-new",
      },
    } satisfies Resource;
    await act(async () => {
      useTutorStore.getState().rehydrateJobFromDetail({
        job_id: "parent-new",
        capability: "resource_generation",
        status: "succeeded",
        message_preview: "",
        created_at: "2026-07-17T00:00:00Z",
        events: [],
        event_count: 0,
        children: [newRepairChild],
      });
      setCanonicalResource(newCanonicalRepair);
      await Promise.resolve();
    });

    expect(mocks.getJobDetail).toHaveBeenNthCalledWith(
      2,
      "local-user",
      "parent-new",
    );
    expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();
  });

  it("restores active repair polling after unmount and remount", async () => {
    vi.useFakeTimers();
    const repairing = {
      ...failedResource,
      format_specific: {
        ...failedResource.format_specific,
        repair_status: "running",
        repair_job_id: "retry-child",
      },
    } satisfies Resource;
    setCanonicalResource(repairing);
    setChildren(childSummary("failed"), retryChild("running"));
    mocks.getJobDetail.mockResolvedValue(parentDetail("running"));

    const view = render(<VideoViewer resource={repairing} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(mocks.getJobDetail).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);

    render(<VideoViewer resource={repairing} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(mocks.getJobDetail).toHaveBeenCalledTimes(2);
    expect(screen.getByText("原始 Manim 渲染失败")).toBeInTheDocument();
    expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();
  });

  it("hydrates a backend restart snapshot and refreshes the canonical package after terminal", async () => {
    vi.useFakeTimers();
    const restartSnapshot = {
      ...failedResource,
      format_specific: {
        ...failedResource.format_specific,
        repair_status: "pending",
        repair_job_id: "retry-child",
      },
    } satisfies Resource;
    setCanonicalResource(restartSnapshot);
    setChildren(childSummary("failed"), retryChild("running"));
    mocks.getJobDetail.mockResolvedValueOnce(parentDetail("succeeded"));
    mocks.getResourcePackageDetail.mockResolvedValueOnce(
      packageWith(readyResource),
    );

    render(<VideoViewer resource={restartSnapshot} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.getJobDetail).toHaveBeenCalledWith("local-user", "parent");
    expect(mocks.getResourcePackageDetail).toHaveBeenCalledWith(
      "local-user",
      "pkg-1",
    );
    expect(document.querySelector("source")).toHaveAttribute(
      "src",
      "/static/manim/retry.mp4",
    );
  });

  it("recovers after a transient job polling failure", async () => {
    vi.useFakeTimers();
    setChildren(childSummary("failed"));
    mocks.getJobDetail
      .mockRejectedValueOnce(new Error("temporary poll failure"))
      .mockResolvedValueOnce(parentDetail("running"))
      .mockResolvedValueOnce(parentDetail("succeeded"));
    mocks.getResourcePackageDetail.mockResolvedValue(
      packageWith(readyResource),
    );
    render(<VideoViewer resource={baseResource} />);

    await beginRetry();

    expect(screen.getByText(/temporary poll failure/)).toBeInTheDocument();
    expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(mocks.getJobDetail).toHaveBeenCalledTimes(2);
    expect(
      useTutorStore.getState().jobsById.parent.children?.at(-1)?.status,
    ).toBe("running");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(mocks.getJobDetail).toHaveBeenCalledTimes(3);
    expect(document.querySelector("source")).toHaveAttribute(
      "src",
      "/static/manim/retry.mp4",
    );
    expect(screen.queryByText(/temporary poll failure/)).not.toBeInTheDocument();
  });

  it("retries terminal package refresh independently without re-polling", async () => {
    vi.useFakeTimers();
    setChildren(childSummary("failed"));
    mocks.getJobDetail
      .mockRejectedValueOnce(new Error("temporary poll failure one"))
      .mockRejectedValueOnce(new Error("temporary poll failure two"))
      .mockResolvedValueOnce(parentDetail("succeeded"));
    mocks.getResourcePackageDetail
      .mockRejectedValueOnce(new Error("temporary package refresh failure"))
      .mockResolvedValueOnce(packageWith(readyResource));
    render(<VideoViewer resource={baseResource} />);

    await beginRetry();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(
      screen.getByText(/temporary package refresh failure/),
    ).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(mocks.getJobDetail).toHaveBeenCalledTimes(3);
    expect(mocks.getResourcePackageDetail).toHaveBeenCalledTimes(2);
    expect(document.querySelector("source")).toHaveAttribute(
      "src",
      "/static/manim/retry.mp4",
    );
  });

  it("exposes a visible recovery action after repeated polling failures", async () => {
    vi.useFakeTimers();
    setChildren(childSummary("failed"));
    mocks.getJobDetail
      .mockRejectedValueOnce(new Error("permanent poll failure"))
      .mockRejectedValueOnce(new Error("permanent poll failure"))
      .mockRejectedValueOnce(new Error("permanent poll failure"))
      .mockResolvedValueOnce(parentDetail("succeeded"));
    mocks.getResourcePackageDetail.mockResolvedValue(
      packageWith(readyResource),
    );
    render(<VideoViewer resource={baseResource} />);

    await beginRetry();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(screen.getByText(/permanent poll failure/)).toBeInTheDocument();
    expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();
    const recover = screen.getByRole("button", {
      name: "继续同步视频状态",
    });
    expect(vi.getTimerCount()).toBe(0);

    fireEvent.click(recover);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.getJobDetail).toHaveBeenCalledTimes(4);
    expect(document.querySelector("source")).toHaveAttribute(
      "src",
      "/static/manim/retry.mp4",
    );
  });

  it("settles the active polling sleep when unmounted", async () => {
    vi.useFakeTimers();
    setChildren(childSummary("failed"));
    mocks.getJobDetail.mockResolvedValue(parentDetail("running"));
    const view = render(<VideoViewer resource={baseResource} />);

    await beginRetry();
    expect(mocks.getJobDetail).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);

    view.unmount();
    await act(async () => {
      await Promise.resolve();
    });
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(mocks.getJobDetail).toHaveBeenCalledTimes(1);
    expect(mocks.getResourcePackageDetail).not.toHaveBeenCalled();
  });

  it("cancelling the polling primitive settles its active wait", async () => {
    vi.useFakeTimers();
    const delay = createRetryPollingDelay(1_000);
    let settled = false;
    void delay.wait.then(() => {
      settled = true;
    });

    delay.cancel();
    await act(async () => {
      await Promise.resolve();
    });

    expect(settled).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("settles the old sleep on dependency change without a stale update", async () => {
    vi.useFakeTimers();
    setChildren(childSummary("failed"));
    let resolveReplacement: (value: ReturnType<typeof parentDetail>) => void =
      () => {};
    mocks.getJobDetail
      .mockResolvedValueOnce(parentDetail("running"))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveReplacement = resolve;
        }),
      );
    const view = render(<VideoViewer resource={baseResource} />);

    await beginRetry();
    expect(vi.getTimerCount()).toBe(1);
    await act(async () => {
      useTutorStore.setState({ userId: "replacement-user" });
      await Promise.resolve();
    });

    expect(mocks.getJobDetail).toHaveBeenNthCalledWith(
      2,
      "replacement-user",
      "parent",
    );
    expect(vi.getTimerCount()).toBe(0);
    view.unmount();
    await act(async () => {
      resolveReplacement(parentDetail("succeeded"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.getResourcePackageDetail).not.toHaveBeenCalled();
    expect(
      useTutorStore.getState().jobsById.parent.children?.at(-1)?.status,
    ).toBe("running");
  });

  it("surfaces retry request failure and leaves terminal failure visible", async () => {
    setChildren(childSummary("failed"));
    mocks.retryVideoRender.mockRejectedValueOnce(new Error("network down"));
    render(<VideoViewer resource={baseResource} />);

    fireEvent.click(
      screen.getByRole("button", { name: "智能修复并重新渲染" }),
    );

    expect(await screen.findByText("network down")).toBeInTheDocument();
    expect(screen.getByText("渲染失败")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });
});
