import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import type { Resource } from "@/lib/types";
import { ResourceCard, ResourceDetail } from "./ResourceCard";

const api = vi.hoisted(() => ({
  recordLearningEvent: vi.fn().mockResolvedValue({}),
  retryJob: vi.fn().mockResolvedValue({ job_id: "retry-1" }),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/lib/store", () => ({
  useTutorStore: (
    selector: (state: { userId: string; latestPackage: null }) => unknown,
  ) => selector({ userId: "local-user", latestPackage: null }),
}));

afterEach(() => {
  cleanup();
  api.recordLearningEvent.mockClear();
  api.retryJob.mockClear();
});

describe("ResourceDetail evidence", () => {
  it("keeps sources and caveats while hiding internal review details", () => {
    render(<ResourceDetail resource={resourceWithEvidence()} />);

    const evidence = screen.getByTestId("resource-evidence");
    expect(evidence).toHaveTextContent("来源与说明");
    expect(evidence).toHaveTextContent("Attention Is All You Need");
    expect(evidence).not.toHaveTextContent("pass");
    expect(evidence).not.toHaveTextContent("safe");
    expect(evidence).not.toHaveTextContent("Agent");
    expect(evidence).not.toHaveTextContent("置信");
    expect(evidence).toHaveTextContent("需要按具体模型文档确认");
  });
});

describe("ResourceCard missing artifact recovery", () => {
  it("shows the missing state and retries the original resource contract", async () => {
    const resource = baseResource({
      resource_id: "resource-1",
      type: "code",
      title: "XOR",
      topic: "XOR",
      metadata: {
        artifact_missing: true,
        recovery_contract: {
          job_id: "job-original",
          resource_types: ["code"],
        },
      },
    });

    render(<ResourceCard resource={resource} />);

    expect(screen.getByText("资源文件缺失")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新生成资源" }));
    await waitFor(() =>
      expect(api.retryJob).toHaveBeenCalledWith(
        "local-user",
        "job-original",
        ["code"],
      ),
    );
  });
});

function resourceWithEvidence(): Resource {
  return baseResource({
    resource_id: "res-doc",
    title: "注意力机制讲解",
    content: "正文",
    difficulty: 3,
    estimated_minutes: 12,
    topic: "Transformer 与注意力机制",
    tags: ["attention"],
    generated_by: ["ContentExpertAgent", "QualityReviewerAgent"],
    confidence_score: 0.91,
    citations: [
      {
        title: "Attention Is All You Need",
        url: "https://arxiv.org/abs/1706.03762",
        source: "paper",
      },
    ],
    review: {
      verdict: "pass",
      quality_score: 0.9,
      issues: [],
      suggestions: [],
      reviewer: "QualityReviewerAgent",
    },
    safety: {
      verdict: "safe",
      risk_level: "low",
    },
    unverified_claims: ["需要按具体模型文档确认"],
  });
}

function baseResource(overrides: Partial<Resource> = {}): Resource {
  return {
    resource_id: "resource-doc",
    type: "document",
    title: "课程资料",
    content: "",
    format_specific: {},
    difficulty: 2,
    estimated_minutes: 5,
    prerequisites: [],
    generated_by: [],
    confidence_score: 0.7,
    topic: "课程主题",
    tags: [],
    created_at: "2026-07-17T00:00:00Z",
    metadata: {},
    ...overrides,
  };
}
