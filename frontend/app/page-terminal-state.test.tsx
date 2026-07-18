import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ActiveTurn } from "@/lib/store";
import type { ClientJob } from "@/lib/job-reducer";

const useTutorStoreMock = vi.fn();

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: unknown) => unknown) =>
    useTutorStoreMock(selector),
}));
vi.mock("@/hooks/useWebSocket", () => ({ useWebSocket: vi.fn() }));
vi.mock("@/components/chat/ChatComposer", () => ({ ChatComposer: () => null }));
vi.mock("@/components/chat/ChatMessages", () => ({ ChatMessages: () => null }));
vi.mock("@/components/chat/JobTray", () => ({ JobTray: () => null }));
vi.mock("@/components/profile/ProfilePanel", () => ({ ProfilePanel: () => null }));
vi.mock("@/components/resources/ResourceTray", () => ({ ResourceTray: () => null }));
vi.mock("@/components/resources/ResourceCard", () => ({
  ResourceDetail: () => null,
  ResourceEmptyDetail: () => null,
}));
vi.mock("@/components/kg/PathVisualizer", () => ({ PathVisualizer: () => null }));
vi.mock("@/components/tutor/TutorPanel", () => ({ TutorPanel: () => null }));
vi.mock("@/components/assessment/AssessmentPanel", () => ({ AssessmentPanel: () => null }));
vi.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/layout/SettingsModal", () => ({ SettingsModal: () => null }));

import HomePage from "./page";

const staleTurn: ActiveTurn = {
  turn_id: "turn-stale",
  phase: "streaming",
  started_at: 1,
  events: [],
  text_buffer: "",
  thinking_buffer: "",
  result: null,
  error: null,
};

function terminalJob(): ClientJob {
  return {
    job_id: "job-terminal",
    capability: "tutoring",
    status: "succeeded",
    message_preview: "hello",
    submitted_at: 1,
    started_at: 1,
    finished_at: 2,
    last_seq: 1,
    events: [],
    result: null,
    error: null,
    event_count: 1,
    seen_event_ids: new Set(),
    text_buffer: "",
    thinking_buffer: "",
    stage: "",
    open_stages: [],
    children: [],
    background_status: null,
  };
}

function mockState() {
  const state = {
    sessionId: "session-1",
    sessionOrigin: "server" as "none" | "draft" | "restored" | "server",
    userId: "local-user",
    messages: [{ id: "m1" }],
    activeTurn: staleTurn,
    jobsById: { "job-terminal": terminalJob() },
    jobOrder: ["job-terminal"],
    latestPackage: null,
    latestTutorAnswer: null,
    latestAssessment: null,
    resourceSelection: { selectedResourceId: null },
    plannedPath: null as any,
    plannedPathOwnerId: null as string | null,
    hydrateTheme: vi.fn(),
    hydrateSessionId: vi.fn(),
    loadConversationAggregate: vi.fn().mockResolvedValue(undefined),
  };
  useTutorStoreMock.mockImplementation((selector: (value: typeof state) => unknown) =>
    selector(state),
  );
  return state;
}

describe("HomePage durable terminal state", () => {
  afterEach(() => {
    cleanup();
    useTutorStoreMock.mockReset();
  });

  it("stops the header spinner when the durable job is terminal despite stale activeTurn", () => {
    mockState();
    render(<HomePage />);
    expect(screen.queryByText("处理中")).not.toBeInTheDocument();
  });

  it("does not request an aggregate for a newly minted draft session", () => {
    const state = mockState();
    state.messages = [];
    state.sessionOrigin = "draft";

    render(<HomePage />);

    expect(state.loadConversationAggregate).not.toHaveBeenCalled();
  });

  it("shows the header spinner for a durable running job despite terminal-looking activeTurn", () => {
    mockState();
    const implementation = useTutorStoreMock.getMockImplementation();
    useTutorStoreMock.mockImplementation((selector: (value: any) => unknown) => {
      const value = implementation?.((x: unknown) => x) as any;
      value.activeTurn = { ...value.activeTurn, phase: "success" };
      value.jobsById["job-terminal"] = {
        ...value.jobsById["job-terminal"],
        status: "running",
        finished_at: null,
      };
      return selector(value);
    });

    render(<HomePage />);
    expect(screen.getByText("处理中")).toBeInTheDocument();
  });

  it("hides the path badge after switching away from the cached path owner", () => {
    const state = mockState();
    state.userId = "a";
    state.plannedPath = { path_id: "path-a" };
    state.plannedPathOwnerId = "a";
    const { rerender } = render(<HomePage />);
    expect(within(screen.getByRole("button", { name: /路径/ })).getByText("1")).toBeInTheDocument();

    state.userId = "b";
    rerender(<HomePage />);

    expect(within(screen.getByRole("button", { name: /路径/ })).queryByText("1")).not.toBeInTheDocument();
  });
});
