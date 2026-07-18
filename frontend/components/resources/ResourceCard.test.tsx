import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ResourceDetail } from "./ResourceCard";
import type { Resource } from "@/lib/types";

describe("ResourceDetail evidence", () => {
  afterEach(() => cleanup());

  it("keeps sources and caveats while hiding internal review details", () => {
    render(<ResourceDetail resource={resourceWithEvidence()} />);

    expect(screen.getByTestId("resource-evidence")).toHaveTextContent("来源与说明");
    expect(screen.getByTestId("resource-evidence")).toHaveTextContent(
      "Attention Is All You Need",
    );
    expect(screen.getByTestId("resource-evidence")).not.toHaveTextContent("pass");
    expect(screen.getByTestId("resource-evidence")).not.toHaveTextContent("safe");
    expect(screen.getByTestId("resource-evidence")).not.toHaveTextContent("Agent");
    expect(screen.getByTestId("resource-evidence")).toHaveTextContent(
      "需要按具体模型文档确认",
    );
  });
});

function resourceWithEvidence(): Resource {
  return {
    resource_id: "res-doc",
    type: "document",
    title: "注意力机制讲解",
    content: "正文",
    format_specific: {},
    difficulty: 3,
    estimated_minutes: 12,
    prerequisites: [],
    generated_by: ["ContentExpertAgent", "QualityReviewerAgent"],
    confidence_score: 0.91,
    topic: "Transformer 与注意力机制",
    tags: ["attention"],
    created_at: "2026-07-15T00:00:00.000Z",
    metadata: {},
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
  };
}
