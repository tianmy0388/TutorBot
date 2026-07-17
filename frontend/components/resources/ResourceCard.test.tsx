import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ResourceCard } from "./ResourceCard";
import type { Resource } from "@/lib/types";

const { retryJob } = vi.hoisted(() => ({
  retryJob: vi.fn().mockResolvedValue({ job_id: "retry-1" }),
}));
vi.mock("@/lib/api", () => ({ retryJob }));
vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: { userId: string }) => unknown) =>
    selector({ userId: "local-user" }),
}));

afterEach(() => {
  cleanup();
  retryJob.mockClear();
});

describe("ResourceCard missing artifact recovery", () => {
  it("shows the missing state and retries the original resource contract", async () => {
    const resource = {
      resource_id: "resource-1",
      type: "code",
      title: "XOR",
      content: "",
      format_specific: {},
      difficulty: 2,
      estimated_minutes: 5,
      prerequisites: [],
      generated_by: [],
      confidence_score: 0.7,
      topic: "XOR",
      tags: [],
      created_at: "2026-07-17T00:00:00Z",
      metadata: {
        artifact_missing: true,
        recovery_contract: {
          job_id: "job-original",
          resource_types: ["code"],
        },
      },
    } satisfies Resource;

    render(<ResourceCard resource={resource} />);

    expect(screen.getByText("资源文件缺失")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新生成资源" }));
    await waitFor(() =>
      expect(retryJob).toHaveBeenCalledWith(
        "local-user",
        "job-original",
        ["code"],
      ),
    );
  });
});
