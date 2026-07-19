import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const useTutorStoreMock = vi.fn();

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: unknown) => unknown) =>
    useTutorStoreMock(selector),
}));

import { TutorPanel } from "./TutorPanel";

const ANSWER = {
  tldr: "自注意力就是给每个词分配关注点。",
  intuition: "像读书时划重点。",
  principle: "softmax(QK^T/√d)V",
  example: "指代消解示例",
  follow_up_suggestion: "追问多头注意力",
  related_concepts: ["transformer"],
  full_markdown: "## 公式\n\n注意力分数：$E=mc^2$",
  confidence: 0.9,
  sources: [],
};

function mockStore(overrides: Record<string, unknown>) {
  useTutorStoreMock.mockImplementation(
    (selector: (value: unknown) => unknown) =>
      selector({
        latestUnderstanding: null,
        latestTutorAnswer: null,
        latestEnrichments: [],
        ...overrides,
      }),
  );
}

describe("TutorPanel", () => {
  afterEach(() => {
    cleanup();
    useTutorStoreMock.mockReset();
  });

  it("renders with answer only (no understanding) after hydration", () => {
    mockStore({ latestTutorAnswer: ANSWER });
    render(<TutorPanel />);
    expect(screen.getByText("问题讲解")).toBeInTheDocument();
    expect(
      screen.getByText("自注意力就是给每个词分配关注点。"),
    ).toBeInTheDocument();
  });

  it("renders full_markdown through the LaTeX pipeline", () => {
    mockStore({ latestTutorAnswer: ANSWER });
    const { container } = render(<TutorPanel />);
    expect(screen.getByText("完整讲解")).toBeInTheDocument();
    // rehype-katex emits <span class="katex"> for $...$ math.
    expect(container.querySelector(".katex")).not.toBeNull();
  });

  it("shows the empty state without an answer", () => {
    mockStore({});
    render(<TutorPanel />);
    expect(screen.getByText("暂无答疑结果")).toBeInTheDocument();
  });
});
