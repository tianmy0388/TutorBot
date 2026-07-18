import { render, screen, waitFor } from "@testing-library/react";
import { vi, describe, expect, it, beforeEach } from "vitest";
import mermaid from "mermaid";
import { MindMapViewer } from "./MindMapViewer";
import type { Resource } from "@/lib/types";

vi.mock("mermaid", () => ({
  default: { initialize: vi.fn(), render: vi.fn() },
}));

const renderMermaid = vi.mocked(mermaid.render);
const resource = (dsl: string): Resource => ({
  resource_id: "mindmap-1",
  type: "mindmap",
  title: "反向传播",
  content: "",
  format_specific: {
    mermaid_dsl: dsl,
    central_topic: "反向传播",
    outline: [
      { depth: 0, label: "反向传播" },
      { depth: 1, label: "激活函数" },
    ],
  },
  difficulty: 2,
  estimated_minutes: 2,
  prerequisites: [],
  generated_by: [],
  confidence_score: 1,
  topic: "反向传播",
  tags: [],
  created_at: "2026-01-01T00:00:00Z",
  metadata: {},
});

describe("MindMapViewer", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the outline on render failure and clears the old error after a valid rerender", async () => {
    renderMermaid.mockRejectedValueOnce(new Error("parser details"));
    const view = render(<MindMapViewer resource={resource("mindmap\n  root((X))")} />);

    expect(await screen.findByText("思维导图暂时无法显示。"))
      .toBeInTheDocument();
    expect(screen.getByText("激活函数")).toBeInTheDocument();
    expect(screen.queryByText("parser details")).not.toBeInTheDocument();

    renderMermaid.mockResolvedValueOnce({
      svg: "<svg>ok</svg>",
      bindFunctions: undefined,
      diagramType: "mindmap",
    });
    view.rerender(<MindMapViewer resource={resource("mindmap\n  root((Y))")} />);

    await waitFor(() => expect(screen.queryByText("思维导图暂时无法显示。")).not.toBeInTheDocument());
  });
});
