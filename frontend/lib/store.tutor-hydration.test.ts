import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  getConversationAggregate: vi.fn(),
}));

import { getConversationAggregate } from "./api";
import { useTutorStore } from "./store";

const ANSWER = {
  tldr: "自注意力就是给每个词分配关注点。",
  intuition: "像读书时划重点。",
  principle: "softmax(QK^T/√d)V",
  example: "指代消解示例",
  follow_up_suggestion: "追问多头注意力",
  related_concepts: ["transformer"],
  full_markdown: "# 自注意力\n\n$E=mc^2$",
  confidence: 0.9,
  sources: [],
};

function aggregateWith(messages: Array<Record<string, unknown>>) {
  return {
    conversation: {
      session_id: "sess-1",
      user_id: "u1",
      title: "t",
      message_count: messages.length,
      last_message_preview: "",
      web_search_enabled: false,
      created_at: "2026-07-19T00:00:00Z",
      updated_at: "2026-07-19T00:00:00Z",
      messages,
    },
    jobs: [],
    packages: [],
    profile_summary: {},
    path_summary: {},
    recovery_warnings: [],
  };
}

describe("loadConversationAggregate tutor hydration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useTutorStore.setState({
      sessionId: "sess-1",
      messages: [],
      latestUnderstanding: null,
      latestTutorAnswer: null,
      latestEnrichments: [],
    });
  });

  it("hydrates latestTutorAnswer from the newest tutor_answer message", async () => {
    (getConversationAggregate as ReturnType<typeof vi.fn>).mockResolvedValue(
      aggregateWith([
        {
          id: "m1",
          role: "assistant",
          content: ANSWER.tldr,
          job_id: "job-1",
          capability: "tutoring",
          created_at: "2026-07-19T00:00:01Z",
          metadata: {
            kind: "tutor_answer",
            job_id: "job-1",
            client_message_id: "terminal:job-1",
            answer: ANSWER,
          },
        },
      ]),
    );

    await useTutorStore
      .getState()
      .loadConversationAggregate("u1", "sess-1");

    const state = useTutorStore.getState();
    expect(state.latestTutorAnswer).toEqual(ANSWER);
    expect(state.latestUnderstanding).toBeNull();
  });

  it("keeps latestTutorAnswer null when no tutor_answer message exists", async () => {
    (getConversationAggregate as ReturnType<typeof vi.fn>).mockResolvedValue(
      aggregateWith([
        {
          id: "m1",
          role: "user",
          content: "解释自注意力",
          created_at: "2026-07-19T00:00:01Z",
          metadata: {},
        },
      ]),
    );

    await useTutorStore
      .getState()
      .loadConversationAggregate("u1", "sess-1");

    expect(useTutorStore.getState().latestTutorAnswer).toBeNull();
  });

  it("skips malformed tutor_answer metadata without failing the load", async () => {
    (getConversationAggregate as ReturnType<typeof vi.fn>).mockResolvedValue(
      aggregateWith([
        {
          id: "m1",
          role: "assistant",
          content: "broken",
          created_at: "2026-07-19T00:00:01Z",
          metadata: { kind: "tutor_answer", answer: "not-an-object" },
        },
      ]),
    );

    await useTutorStore
      .getState()
      .loadConversationAggregate("u1", "sess-1");

    const state = useTutorStore.getState();
    expect(state.latestTutorAnswer).toBeNull();
    expect(state.messages).toHaveLength(1);
  });
});
