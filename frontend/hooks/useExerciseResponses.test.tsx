import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useExerciseResponses } from "./useExerciseResponses";

const api = vi.hoisted(() => ({
  getExerciseResponseState: vi.fn(),
  putExerciseDraft: vi.fn(),
  submitExerciseResponse: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

const identity = {
  userId: "learner-1",
  packageId: "package-1",
  resourceId: "resource-1",
  sessionId: "session-1",
};

const emptyState = { draft: null, submissions: [] };

describe("useExerciseResponses", () => {
  beforeEach(() => {
    api.getExerciseResponseState.mockReset().mockResolvedValue(emptyState);
    api.putExerciseDraft.mockReset().mockResolvedValue({});
    api.submitExerciseResponse.mockReset().mockResolvedValue({
      submission_id: "submission-1",
      question_id: "q1",
      answer_json: "B",
      correct: true,
      score: 1,
      grading_status: "auto_graded",
    });
  });

  it("restores a draft after remount and keeps it keyed by question id", async () => {
    api.getExerciseResponseState.mockResolvedValue({
      draft: { question_id: "q1", answer_json: "B" },
      submissions: [],
    });
    const first = renderHook(() => useExerciseResponses(identity, ["q1"]));
    await waitFor(() => expect(first.result.current.drafts.q1).toBe("B"));
    first.unmount();

    const second = renderHook(() => useExerciseResponses(identity, ["q1"]));
    await waitFor(() => expect(second.result.current.drafts.q1).toBe("B"));
    expect(api.getExerciseResponseState).toHaveBeenCalledWith(
      "package-1", "resource-1", "q1", "learner-1", expect.anything(),
    );
  });

  it("does not submit or score while drafting", async () => {
    const view = renderHook(() => useExerciseResponses(identity, ["q1"]));
    await act(async () => { view.result.current.setDraft("q1", "B"); });

    expect(api.submitExerciseResponse).not.toHaveBeenCalled();
    await act(async () => { await view.result.current.submit("q1"); });
    expect(api.submitExerciseResponse).toHaveBeenCalledOnce();
  });

  it("aborts the old resource load and ignores its response after an identity switch", async () => {
    let resolveOld!: (value: { draft: { question_id: string; answer_json: string } | null; submissions: unknown[] }) => void;
    api.getExerciseResponseState
      .mockReturnValueOnce(new Promise<{ draft: { question_id: string; answer_json: string } | null; submissions: unknown[] }>((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({
        draft: { question_id: "q1", answer_json: "new" }, submissions: [],
      });
    const view = renderHook(
      ({ value }) => useExerciseResponses(value, ["q1"]),
      { initialProps: { value: identity } },
    );
    const next = { ...identity, resourceId: "resource-2" };
    view.rerender({ value: next });
    await waitFor(() => expect(view.result.current.drafts.q1).toBe("new"));
    resolveOld({ draft: { question_id: "q1", answer_json: "old" }, submissions: [] });
    await waitFor(() => expect(view.result.current.drafts.q1).toBe("new"));
    expect(api.getExerciseResponseState.mock.calls[0][4].aborted).toBe(true);
  });
});
