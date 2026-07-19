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
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

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
  messages?: Array<{ id: string; role: string; content: string; timestamp: number; metadata?: Record<string, unknown> }>;
  recoveryWarnings?: Array<{ code: string; message: string }>;
  dismissRecoveryWarning?: (index: number) => void;
} = {}) {
  const activeTurn: ActiveTurn = {
    ...baseActiveTurn,
    ...(opts.activeTurn ?? {}),
  } as ActiveTurn;
  const jobsById = opts.jobs?.jobsById ?? {};
  const jobOrder = opts.jobs?.jobOrder ?? Object.keys(jobsById);
  useTutorStoreMock.mockImplementation((selector: (s: unknown) => unknown) =>
    selector({
      messages: opts.messages ?? [],
      activeTurn,
      jobsById,
      jobOrder,
      tracePanelOpen: false,
      recoveryWarnings: opts.recoveryWarnings ?? [],
      dismissRecoveryWarning: opts.dismissRecoveryWarning ?? vi.fn(),
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
    // stale loading indicator whenever activeTurn.phase !== idle
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
    expect(screen.queryByText("准备学习内容")).not.toBeInTheDocument();
  });

  it("renders a terminal workflow card with completed stages", () => {
    mockStoreState({
      messages: [{
        id: "workflow:job-1",
        role: "assistant",
        content: "",
        timestamp: 1,
        metadata: {
          kind: "workflow_timeline",
          job_id: "job-1",
          workflow: { status: "succeeded", stages: [{ name: "intent_understanding", status: "completed" }] },
        },
      }],
    });

    render(<ChatMessages />);

    expect(screen.getByText(/已完成/)).toBeInTheDocument();
    expect(screen.getByText("理解目标")).toBeInTheDocument();
    expect(screen.queryByText("准备学习内容")).not.toBeInTheDocument();
  });

  it("ignores malformed workflow metadata instead of rendering an invalid stage", () => {
    mockStoreState({
      messages: [{
        id: "workflow:broken",
        role: "assistant",
        content: "fallback content",
        timestamp: 1,
        metadata: {
          kind: "workflow_timeline",
          workflow: { status: "not-terminal", stages: [null] },
        },
      }],
    });

    render(<ChatMessages />);

    expect(screen.getByText("fallback content")).toBeInTheDocument();
  });

  it("trusts a canonical terminal event when the replayed status is stale", () => {
    const job = {
      ...baseTerminalJob("job-canonical"),
      status: "running" as const,
      finished_at: null,
    };
    mockStoreState({
      activeTurn: { phase: "streaming" },
      jobs: { jobsById: { [job.job_id]: job }, jobOrder: [job.job_id] },
    });

    render(<ChatMessages />);

    expect(screen.queryByText("准备学习内容")).not.toBeInTheDocument();
  });

  it("does not render stale activeTurn buffers for a durable running job", () => {
    const job = {
      ...baseTerminalJob("job-running"),
      status: "running" as const,
      finished_at: null,
      events: [],
    };
    mockStoreState({
      activeTurn: {
        phase: "streaming",
        text_buffer: "来自旧 activeTurn 的错误内容",
      },
      jobs: { jobsById: { [job.job_id]: job }, jobOrder: [job.job_id] },
    });

    render(<ChatMessages />);

    expect(screen.queryByText("来自旧 activeTurn 的错误内容")).not.toBeInTheDocument();
    expect(screen.getByText(/请稍等一下/)).toBeInTheDocument();
  });

  it("renders the newest nonterminal job from newest-first jobOrder", () => {
    const newest = {
      ...runningJob("job-newest", ""),
      events: [progressEvent("最新任务输出", "e-newest")],
    };
    const older = {
      ...runningJob("job-older", ""),
      events: [progressEvent("旧任务输出", "e-older")],
    };
    mockStoreState({
      jobs: {
        jobsById: {
          [newest.job_id]: newest,
          [older.job_id]: older,
        },
        jobOrder: [newest.job_id, older.job_id],
      },
    });

    render(<ChatMessages />);

    expect(screen.getByText("最新任务输出")).toBeInTheDocument();
    expect(screen.queryByText("旧任务输出")).not.toBeInTheDocument();
  });

  it("renders live progress events from jobsById on a non-terminal job", () => {
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
      events: [progressEvent("self-attention 计算 QKV 注意力。", "e-progress-1")],
      result: null,
      error: null,
      text_buffer: "",
      thinking_buffer: "",
      stage: "",
      open_stages: [],
    };
    // While the job is non-terminal, ChatMessages renders the live
    // task-process card. The per-job event stream is authoritative and
    // its progress texts must be visible.
    mockStoreState({
      activeTurn: {
        turn_id: "t1",
        phase: "streaming",
        started_at: now - 1000,
        text_buffer: "",
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
    // The progress line may be split across sub-elements (bullet span);
    // the easiest robust assertion is to check the container's
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

  it("renders a structured live-job error without coercing it to an object string", () => {
    const job = runningJob("job-structured-error", "");
    job.error = {
      code: "INVALID_SCOPE",
      message: "请选择检索范围",
      details: { kind: null },
    };
    mockStoreState({
      jobs: { jobsById: { [job.job_id]: job }, jobOrder: [job.job_id] },
    });

    render(<ChatMessages />);

    expect(screen.getByText("请选择检索范围")).toBeInTheDocument();
    expect(screen.getByText("错误编号：INVALID_SCOPE")).toBeInTheDocument();
  });

  it("shows recovery warnings as non-blocking dismissible notices", () => {
    const dismiss = vi.fn();
    mockStoreState({
      recoveryWarnings: [
        { code: "missing_artifact", message: "资源文件缺失，可重新生成。" },
      ],
      dismissRecoveryWarning: dismiss,
    });

    render(<ChatMessages />);

    expect(screen.getByText("资源文件缺失，可重新生成。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭恢复提示" }));
    expect(dismiss).toHaveBeenCalledWith(0);
  });
});

function baseTerminalJob(jobId: string): ClientJob {
  const now = Date.now();
  return {
    job_id: jobId,
    capability: "tutoring",
    status: "succeeded",
    message_preview: "hello",
    submitted_at: now - 1000,
    started_at: now - 1000,
    finished_at: now,
    last_seq: 1,
    event_count: 1,
    seen_event_ids: new Set(),
    events: [
      {
        type: "job_terminal",
        source: "job_runner",
        stage: "terminal",
        content: "done",
        metadata: { job_id: jobId },
        session_id: "s1",
        turn_id: "",
        seq: 1,
        timestamp: now / 1000,
        event_id: `terminal-${jobId}`,
      },
    ],
    result: null,
    error: null,
    text_buffer: "",
    thinking_buffer: "",
    stage: "",
    open_stages: [],
  };
}

function runningJob(jobId: string, text: string): ClientJob {
  return {
    ...baseTerminalJob(jobId),
    status: "running",
    finished_at: null,
    events: [],
    text_buffer: text,
  };
}

function progressEvent(text: string, eventId: string): ClientJob["events"][number] {
  return {
    type: "progress",
    source: "test",
    stage: "",
    content: "",
    metadata: { message: text },
    session_id: "s1",
    turn_id: "",
    seq: 1,
    timestamp: 1,
    event_id: eventId,
  };
}
