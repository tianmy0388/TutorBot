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
  phase: "idle",
  text_buffer: "",
  thinking_buffer: "",
  events: [],
  result: null,
  error: null,
};

const baseJobs: JobsState = {
  jobsById: {},
  jobOrder: [],
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
      jobId: "job_1",
      status: "succeeded",
      capability: "tutoring",
      stage: "done",
      events: [
        { type: "job_terminal", stage: "done", source: "runner", content: "" },
      ],
      textBuffer: "",
      thinkingBuffer: "",
      result: null as never,
      error: null,
      createdAt: now - 1000,
      startedAt: now - 1000,
      finishedAt: now,
      message: "解释 self-attention",
      language: "zh",
    };
    mockStoreState({
      activeTurn: {
        phase: "success",
        text_buffer: "",
        thinking_buffer: "",
        events: [
          { type: "job_terminal", stage: "done", source: "runner", content: "" },
        ],
        result: null,
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
      jobId: "job_2",
      status: "running",
      capability: "tutoring",
      stage: "answer",
      events: [
        { type: "text", stage: "answer", source: "tutor", content: "self-attention 计算 QKV 注意力。" },
      ],
      textBuffer: "self-attention 计算 QKV 注意力。",
      thinkingBuffer: "",
      result: null as never,
      error: null,
      createdAt: now - 1000,
      startedAt: now - 1000,
      finishedAt: now,
      message: "解释 self-attention",
      language: "zh",
    };
    // While the job is non-terminal, ChatMessages renders the live
    // streaming view. The textBuffer must be visible.
    mockStoreState({
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
});
