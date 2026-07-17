import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PathVisualizer } from "./PathVisualizer";

afterEach(cleanup);

describe("PathVisualizer terminal UI states", () => {
  it("renders loading, empty, and failed states", () => {
    const { rerender } = render(<PathVisualizer path={null} loading />);
    expect(screen.getByText("学习路径加载中…")).toBeInTheDocument();
    rerender(<PathVisualizer path={null} />);
    expect(screen.getByText("暂无学习路径")).toBeInTheDocument();
    rerender(<PathVisualizer path={null} error="加载失败" />);
    expect(screen.getByText("学习路径加载失败")).toBeInTheDocument();
  });

  it("renders a successful persisted path using canonical node id", () => {
    render(<PathVisualizer path={{
      path_id: "p", course: "course", name: "Path", description: "",
      profile_version: 2, edges: [], rationale: "topological",
      nodes: [{ id: "attention", name: "Attention", category: "core", difficulty: 2, estimated_hours: 1, prerequisites: [], status: "available" }],
      total_estimated_hours: 1, completed_count: 0, available_count: 1,
      locked_count: 0, generated_at: new Date().toISOString(),
    }} />);
    expect(screen.getByText("Attention")).toBeInTheDocument();
  });
});
