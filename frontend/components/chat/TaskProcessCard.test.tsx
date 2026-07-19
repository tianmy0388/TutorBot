import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TaskProcessCard } from "./TaskProcessCard";
import type { TaskProcessData } from "@/lib/task-process";

const liveData: TaskProcessData = {
  status: "active",
  stages: [
    { key: "intent_understanding", label: "理解目标", state: "completed" },
    { key: "rag_retrieval", label: "查找课程资料", state: "active" },
    { key: "package_assembly", label: "整理学习资料", state: "pending" },
  ],
  progress: ["正在理解目标", "正在查找课程资料"],
  resourceCount: 2,
  startedAt: Date.now() - 5000,
  finishedAt: null,
  durationMs: null,
  error: null,
};

const doneData: TaskProcessData = {
  status: "succeeded",
  stages: [
    { key: "intent_understanding", label: "理解目标", state: "completed" },
    { key: "rag_retrieval", label: "查找课程资料", state: "completed" },
  ],
  progress: ["正在理解目标", "正在整理学习资料"],
  resourceCount: 6,
  startedAt: null,
  finishedAt: 1752000000000,
  durationMs: 65000,
  error: null,
};

describe("TaskProcessCard", () => {
  afterEach(() => cleanup());

  it("renders the live state: chips, progress stream, resource count", () => {
    render(<TaskProcessCard data={liveData} />);
    expect(screen.getByText("正在进行")).toBeInTheDocument();
    expect(screen.getByText("理解目标")).toBeInTheDocument();
    expect(screen.getByText("查找课程资料")).toBeInTheDocument();
    expect(screen.getByText("整理学习资料")).toBeInTheDocument();
    expect(screen.getByText("正在理解目标")).toBeInTheDocument();
    expect(screen.getByText("正在查找课程资料")).toBeInTheDocument();
    expect(screen.getByText("已产出 2 项资源")).toBeInTheDocument();
  });

  it("renders the completed state with collapsed progress detail", () => {
    render(<TaskProcessCard data={doneData} />);
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("耗时 1 分 5 秒")).toBeInTheDocument();
    expect(screen.getByText("已产出 6 项资源")).toBeInTheDocument();
    // Progress detail is collapsed by default.
    expect(screen.queryByText("正在整理学习资料")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/过程明细/));
    expect(screen.getByText("正在整理学习资料")).toBeInTheDocument();
  });

  it("shows the failed label for a failed job", () => {
    render(<TaskProcessCard data={{ ...doneData, status: "failed" }} />);
    expect(screen.getByText("需要再试一次")).toBeInTheDocument();
  });
});
