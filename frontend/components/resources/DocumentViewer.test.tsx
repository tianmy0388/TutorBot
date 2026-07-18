import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DocumentViewer } from "./DocumentViewer";
import type { Resource } from "@/lib/types";

const resource = (content: string): Resource => ({
  resource_id: "document-1",
  type: "document",
  title: "文档",
  content,
  format_specific: {},
  difficulty: 2,
  estimated_minutes: 2,
  prerequisites: [],
  generated_by: [],
  confidence_score: 1,
  topic: "主题",
  tags: [],
  created_at: "2026-01-01T00:00:00Z",
  metadata: {},
});

describe("DocumentViewer", () => {
  it("does not create an image request for an unresolved relative source", () => {
    render(<DocumentViewer resource={resource("![Dyna](dyna_diagram.png)")} />);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("图片未提供")).toBeInTheDocument();
  });

  it("allows canonical artifact and HTTP image sources", () => {
    render(<DocumentViewer resource={resource("![Artifact](/api/resources/a)\n![Web](https://example.test/a.png)")} />);

    expect(screen.getByAltText("Artifact")).toHaveAttribute("src", "/api/resources/a");
    expect(screen.getByAltText("Web")).toHaveAttribute("src", "https://example.test/a.png");
  });
});
