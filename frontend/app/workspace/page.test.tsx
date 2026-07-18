import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const testState = vi.hoisted(() => ({
  refreshLearningState: vi.fn().mockResolvedValue(undefined),
  hydrateSessionId: vi.fn(),
  setSessionId: vi.fn(),
  resetSession: vi.fn(),
  loadConversationAggregate: vi.fn().mockResolvedValue(undefined),
  storeState: {} as Record<string, unknown>,
}));

vi.mock("@/lib/learning-state", () => ({
  refreshLearningState: testState.refreshLearningState,
}));
vi.mock("@/hooks/useWebSocket", () => ({ useWebSocket: vi.fn() }));
vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector(testState.storeState),
}));

vi.mock("@/components/chat/ChatMessages", () => ({
  ChatMessages: () => <div>任务文档</div>,
}));
vi.mock("@/components/chat/ChatComposer", () => ({
  ChatComposer: () => <div>任务输入</div>,
}));
vi.mock("@/components/chat/JobTray", () => ({ JobTray: () => <div>任务记录</div> }));
vi.mock("@/components/profile/ProfilePanel", () => ({ ProfilePanel: () => <div>学习状态</div> }));
vi.mock("@/components/resources/ResourceTray", () => ({ ResourceTray: () => <div>资料列表</div> }));
vi.mock("@/components/resources/ResourceCard", () => ({
  ResourceDetail: () => <div>资料详情</div>,
  ResourceEmptyDetail: () => <div>选择资料</div>,
}));
vi.mock("@/components/kg/PathVisualizer", () => ({ PathVisualizer: () => <div>下一步</div> }));
vi.mock("@/components/tutor/TutorPanel", () => ({ TutorPanel: () => <div>问题讲解</div> }));
vi.mock("@/components/assessment/AssessmentPanel", () => ({ AssessmentPanel: () => <div>练习回顾</div> }));
vi.mock("@/components/workspace/CourseTaskWorkbench", () => ({
  CourseTaskWorkbench: ({ onCreateTask }: { onCreateTask: () => void }) => (
    <button type="button" onClick={onCreateTask}>新建学习任务</button>
  ),
}));

import WorkspacePage from "@/app/workspace/page";

function createStoreState(overrides: Record<string, unknown> = {}) {
  return {
    sessionId: "session-1",
    currentCourse: "ai_introduction",
    hydrateSessionId: testState.hydrateSessionId,
    setSessionId: testState.setSessionId,
    resetSession: testState.resetSession,
    loadConversationAggregate: testState.loadConversationAggregate,
    userId: "user-1",
    messages: [],
    latestPackage: null,
    latestTutorAnswer: null,
    latestAssessment: null,
    resourceSelection: { selectedResourceId: null },
    plannedPath: null,
    jobsById: {},
    jobOrder: [],
    ...overrides,
  };
}

describe("WorkspacePage", () => {
  beforeEach(() => {
    testState.storeState = createStoreState();
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("switches from the task overview into a new document task", () => {
    render(<WorkspacePage />);
    fireEvent.click(screen.getByRole("button", { name: "新建学习任务" }));

    expect(testState.setSessionId).toHaveBeenCalledTimes(1);
    expect(testState.resetSession).toHaveBeenCalledTimes(1);
    expect(screen.getByText("任务文档")).toBeInTheDocument();
    expect(screen.getByText("任务输入")).toBeInTheDocument();
  });

  it("refreshes learning state after a task reaches a terminal status", async () => {
    testState.storeState = createStoreState({
      jobsById: { completed_job: { status: "succeeded" } },
      jobOrder: ["completed_job"],
    });

    render(<WorkspacePage />);

    await waitFor(() =>
      expect(testState.refreshLearningState).toHaveBeenCalledWith(
        "user-1",
        "ai_introduction",
      ),
    );
  });
});
