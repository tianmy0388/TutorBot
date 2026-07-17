import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { Resource } from "@/lib/types";
import { VideoViewer } from "./VideoViewer";

let backgroundStatus: "succeeded" | "failed" = "failed";
vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: unknown) => unknown) =>
    selector({
      jobsById: {
        parent: {
          children: [
            {
              job_id: "child-video",
              parent_job_id: "parent",
              task_kind: "video_render",
              status: backgroundStatus,
              metadata: { package_id: "pkg-1", resource_id: "video-1" },
            },
          ],
        },
      },
    }),
}));

const resource = {
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

afterEach(() => cleanup());

describe("VideoViewer durable child projection", () => {
  it("renders a failed child after refresh instead of stale rendering state", () => {
    backgroundStatus = "failed";
    render(<VideoViewer resource={resource} />);

    expect(screen.getByText("渲染失败")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });

  it("renders a succeeded child as terminal even when the package snapshot is stale", () => {
    backgroundStatus = "succeeded";
    render(<VideoViewer resource={resource} />);

    expect(screen.getByText("渲染完成")).toBeInTheDocument();
    expect(screen.queryByText("视频渲染中…")).not.toBeInTheDocument();
  });
});
