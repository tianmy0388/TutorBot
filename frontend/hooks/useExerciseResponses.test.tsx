import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

  afterEach(() => cleanup());

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
    await waitFor(() => expect(api.getExerciseResponseState.mock.calls[0][4].aborted).toBe(true));
  });

  it("single-flights a submission, retries a failed request with its id, and rotates ids for a changed answer", async () => {
    api.submitExerciseResponse.mockRejectedValueOnce(new Error("offline"));
    const view = renderHook(() => useExerciseResponses(identity, ["q1"]));
    await act(async () => { view.result.current.setDraft("q1", "B"); });

    const first = view.result.current.submit("q1");
    const second = view.result.current.submit("q1");
    expect(first).toBe(second);
    await act(async () => { await first; });
    expect(api.submitExerciseResponse).toHaveBeenCalledTimes(1);
    const failedId = api.submitExerciseResponse.mock.calls[0][3].client_submission_id;

    await act(async () => { await view.result.current.submit("q1"); });
    expect(api.submitExerciseResponse.mock.calls[1][3].client_submission_id).toBe(failedId);
    await act(async () => { view.result.current.setDraft("q1", "C"); });
    await act(async () => { await view.result.current.submit("q1"); });
    expect(api.submitExerciseResponse.mock.calls[2][3].client_submission_id).not.toBe(failedId);
  });

  it("deduplicates StrictMode loads and aborts an identity with no subscribers", async () => {
    const view = renderHook(() => useExerciseResponses(identity, ["q1"]), { wrapper: StrictMode });
    await waitFor(() => expect(api.getExerciseResponseState).toHaveBeenCalledTimes(1));
    view.unmount();
    await waitFor(() => expect(api.getExerciseResponseState.mock.calls[0][4].aborted).toBe(true));
  });

  it("flushes the latest draft on unmount before its debounce delay", async () => {
    vi.useFakeTimers();
    const view = renderHook(() => useExerciseResponses(identity, ["q1"]));
    act(() => { view.result.current.setDraft("q1", "latest"); });
    view.unmount();
    expect(api.putExerciseDraft).toHaveBeenCalledWith(
      "package-1", "resource-1", "q1",
      { user_id: "learner-1", answer_json: "latest" }, expect.anything(),
    );
    const signal = api.putExerciseDraft.mock.calls[0][4] as AbortSignal;
    act(() => { vi.advanceTimersByTime(1_500); });
    expect(signal.aborted).toBe(true);
    vi.useRealTimers();
  });

  it("partitions submitting state when the resource changes with the same question id", async () => {
    let resolveOld!: (value: unknown) => void;
    let resolveNew!: (value: unknown) => void;
    api.submitExerciseResponse
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveNew = resolve; }));
    const view = renderHook(
      ({ value }) => useExerciseResponses(value, ["q1"]),
      { initialProps: { value: identity } },
    );
    act(() => { view.result.current.setDraft("q1", "old"); });
    act(() => { void view.result.current.submit("q1"); });
    expect(view.result.current.submitting.q1).toBe(true);

    const next = { ...identity, resourceId: "resource-2" };
    view.rerender({ value: next });
    await waitFor(() => expect(view.result.current.submitting.q1).not.toBe(true));
    act(() => { view.result.current.setDraft("q1", "new"); });
    act(() => { void view.result.current.submit("q1"); });
    expect(view.result.current.submitting.q1).toBe(true);
    expect(api.submitExerciseResponse).toHaveBeenCalledTimes(2);

    resolveOld({ submission_id: "old", question_id: "q1", answer_json: "old", grading_status: "auto_graded", correct: false, score: 0 });
    await act(async () => { await Promise.resolve(); });
    expect(view.result.current.submitting.q1).toBe(true);
    resolveNew({ submission_id: "new", question_id: "q1", answer_json: "new", grading_status: "auto_graded", correct: true, score: 1 });
  });

  it("ignores a shared load that resolves after unmount", async () => {
    let resolveLoad!: (value: typeof emptyState) => void;
    api.getExerciseResponseState.mockReturnValue(
      new Promise<typeof emptyState>((resolve) => { resolveLoad = resolve; }),
    );
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = renderHook(() => useExerciseResponses(identity, ["q1"]));
    view.unmount();
    resolveLoad(emptyState);
    await act(async () => { await Promise.resolve(); });
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
  });
});
