import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  appendConversationMessage: vi.fn(),
  getResourcePackageDetail: vi.fn(),
}));

import { appendConversationMessage } from "./api";
import { dispatchStreamEvent } from "./event-handler";
import { useTutorStore } from "./store";

function terminalEvent(jobId: string) {
  return {
    type: "job_terminal",
    source: "job_runner",
    stage: "terminal",
    content: "已就绪",
    metadata: {
      job_id: jobId,
      session_id: "sess-1",
      contract: {
        job_id: jobId,
        capability: "tutoring",
        status: "succeeded",
        assistant_message: "已就绪",
      },
    },
    session_id: "sess-1",
    turn_id: "",
    seq: 10,
    timestamp: 1752000000,
    event_id: `terminal-${jobId}`,
  };
}

describe("dispatchStreamEvent persistence (backend owns writes)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useTutorStore.setState({
      userId: "u1",
      sessionId: "sess-1",
      messages: [],
      jobsById: {},
      jobOrder: [],
    });
    useTutorStore.getState().applyReducerEvent({
      type: "submit",
      job_id: "job-1",
      capability: "tutoring",
      message_preview: "解释自注意力",
    });
  });

  it("does NOT POST assistant/workflow messages on job_terminal", async () => {
    dispatchStreamEvent(terminalEvent("job-1"), {
      sessionId: "sess-1",
      userId: "u1",
    });
    // The pre-Task-5 persists were fire-and-forget floats; give them a
    // tick to (not) land so this assertion actually guards the removal.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(appendConversationMessage).not.toHaveBeenCalled();
  });

  it("still renders the terminal assistant + workflow messages locally", () => {
    dispatchStreamEvent(terminalEvent("job-1"), {
      sessionId: "sess-1",
      userId: "u1",
    });
    const { messages } = useTutorStore.getState();
    expect(
      messages.some(
        (m) => m.role === "assistant" && m.content === "已就绪",
      ),
    ).toBe(true);
    expect(messages.some((m) => m.id === "workflow:job-1")).toBe(true);
  });
});
