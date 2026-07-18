/**
 * Stage 0 — ChatMessages job-state regression test.
 *
 * The plan calls out that ``ChatMessages`` still drives the
 * progress UI off ``activeTurn.phase !== "idle"``, even though the
 * authoritative state is the per-job events in ``jobsById``. After
 * ``job_terminal`` the activeTurn phase is left as ``"success"`` and
 * the spinner hangs forever. This test pins the behaviour: when a
 * job has reached its terminal state, the loading indicator must
 * not render — even if a legacy activeTurn record still claims to
 * be "in progress".
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ChatMessages } from "./ChatMessages";
import type { ActiveTurn } from "@/lib/store";
import type { ClientJob, JobsState } from "@/lib/job-reducer";

// Mock the store with a controlled shape. The point of the test is
// to assert the *rendering* contract, not the store implementation.
const useTutorStoreMock = vi.fn();
vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (s: unknown) => unknown) =>
    useTutorStoreMock(selector),
}));

const baseActiveTurn: ActiveTurn = {
  turn_id: "",
  phase: "idle",
  started_at: 0,
  text_buffer: "",
  thinking_buffer: "",
  events: [],
  result: null,
  error: null,
};

const baseJobs: JobsState = {
  jobsById: {},
  jobOrder: [],
  messages: [],
};

function mockStoreState(opts: {
  activeTurn?: Partial<ActiveTurn>;
  jobs?: { jobsById?: Record<string, ClientJob>; jobOrder?: string[] };
} = {}) {
  const activeTurn: ActiveTurn = {
    ...baseActiveTurn,
    ...(opts.activeTurn ?? {}),
  } as ActiveTurn;
  const jobsById = opts.jobs?.jobsById ?? {};
  const jobOrder = opts.jobs?.jobOrder ?? Object.keys(jobsById);
  useTutorStoreMock.mockImplementation((selector: (s: unknown) => unknown) =>
    selector({
      messages: [],
      activeTurn,
      jobsById,
      jobOrder,
      tracePanelOpen: false,
    }),
  );
}

describe("ChatMessages — terminal state", () => {
  afterEach(() => {
    cleanup();
    useTutorStoreMock.mockReset();
  });

  it("does not show the loading spinner after job_terminal, even if activeTurn.phase === 'success'", () => {
    // Regression: the legacy activeTurn.phase is left at 'success'
    // after a job_terminal. The current ChatMessages renders the
    // "正在调用 Agent…" indicator whenever activeTurn.phase !== idle
    // and all buffers are empty. We construct a state where the
    // authoritative job is terminal but the legacy buffers are
    // empty — exactly the hang scenario.
    const now = Date.now();
    const job: ClientJob = {
      job_id: "job_1",
      capability: "tutoring",
      status: "succeeded",
      message_preview: "解释 self-attention",
      submitted_at: now - 1000,
      started_at: now - 1000,
      finished_at: now,
      last_seq: 1,
      event_count: 1,
      seen_event_ids: new Set(),
      events: [
        {
          type: "job_terminal",
          source: "runner",
          stage: "done",
          content: "",
          metadata: {},
          session_id: "s1",
          turn_id: "",
          seq: 1,
          timestamp: now,
          event_id: "e1",
        },
      ],
      result: null,
      error: null,
      text_buffer: "",
      thinking_buffer: "",
      stage: "",
      open_stages: [],
    };
    mockStoreState({
      activeTurn: {
        turn_id: "",
        phase: "success",
        started_at: now - 1000,
        text_buffer: "",
        thinking_buffer: "",
        events: [
          {
            type: "job_terminal",
            source: "runner",
            stage: "done",
            content: "",
            metadata: {},
            session_id: "s1",
            turn_id: "",
            seq: 1,
            timestamp: now,
            event_id: "e1",
          },
        ],
        result: null,
        error: null,
      },
      jobs: {
        jobsById: { job_1: job },
        jobOrder: ["job_1"],
      },
    });

    render(<ChatMessages />);
    // The "正在调用 Agent" badge must NOT render after a terminal job.
    expect(screen.queryByText(/调用 Agent/i)).not.toBeInTheDocument();
  });

  it("renders the streamed text from jobsById on a succeeded job", () => {
    const now = Date.now();
    const job: ClientJob = {
      job_id: "job_2",
      capability: "tutoring",
      status: "running",
      message_preview: "解释 self-attention",
      submitted_at: now - 1000,
      started_at: now - 1000,
      finished_at: null,
      last_seq: 0,
      event_count: 0,
      seen_event_ids: new Set(),
      events: [],
      result: null,
      error: null,
      text_buffer: "",
      thinking_buffer: "",
      stage: "",
      open_stages: [],
    };
    // While the job is non-terminal, ChatMessages renders the live
    // streaming view. textBuffer lives on activeTurn (the event
    // handler keeps it in lockstep with the WS) and must be visible.
    mockStoreState({
      activeTurn: {
        turn_id: "t1",
        phase: "streaming",
        started_at: now - 1000,
        text_buffer: "self-attention 计算 QKV 注意力。",
        thinking_buffer: "",
        events: [],
        result: null,
        error: null,
      },
      jobs: {
        jobsById: { job_2: job },
        jobOrder: ["job_2"],
      },
    });

    const { container } = render(<ChatMessages />);
    // ReactMarkdown may split the text into sub-elements; the
    // easiest robust assertion is to check the container's
    // textContent.
    expect(container.textContent ?? "").toMatch(/self-attention.*QKV.*注意力/);
  });

  it("renders a natural progress label instead of the internal stage name", () => {
    const now = Date.now();
    const job: ClientJob = {
      job_id: "job_3",
      capability: "resource_generation",
      status: "running",
      message_preview: "整理注意力机制资料",
      submitted_at: now - 1000,
      started_at: now - 1000,
      finished_at: null,
      last_seq: 0,
      event_count: 0,
      seen_event_ids: new Set(),
      events: [],
      result: null,
      error: null,
      text_buffer: "",
      thinking_buffer: "",
      stage: "quality_review_inner",
      open_stages: [],
    };
    mockStoreState({
      activeTurn: {
        turn_id: "t3",
        phase: "streaming",
        started_at: now - 1000,
        text_buffer: "",
        thinking_buffer: "",
        events: [],
        result: null,
        error: null,
      },
      jobs: {
        jobsById: { job_3: job },
        jobOrder: ["job_3"],
      },
    });

    render(<ChatMessages />);
    expect(screen.getByText("检查内容")).toBeInTheDocument();
    expect(screen.queryByText("quality_review_inner")).not.toBeInTheDocument();
  });
});
