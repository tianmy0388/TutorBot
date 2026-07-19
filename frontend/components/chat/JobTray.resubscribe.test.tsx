import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createJobState } from "@/lib/job-reducer";

const useJobQueueMock = vi.fn();
const useTutorStoreMock = vi.fn();

vi.mock("@/hooks/useJobQueue", () => ({
  useJobQueue: (...args: unknown[]) => useJobQueueMock(...args),
}));
vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: unknown) => unknown) =>
    useTutorStoreMock(selector),
}));

import { JobTray } from "./JobTray";

describe("JobTray auto-resubscribe", () => {
  afterEach(() => {
    cleanup();
    useJobQueueMock.mockReset();
    useTutorStoreMock.mockReset();
  });

  it("resubscribes to still-running jobs after hydration", () => {
    const subscribe = vi.fn();
    useJobQueueMock.mockReturnValue({
      jobs: [],
      total: 0,
      loading: false,
      error: null,
      stats: null,
      activeJobs: [],
      refresh: vi.fn(),
      subscribe,
      cancel: vi.fn(),
      remove: vi.fn(),
    });
    const state = createJobState("job-live", "resource_generation");
    state.jobsById["job-live"].status = "running";
    const terminal = createJobState("job-done", "tutoring");
    terminal.jobsById["job-done"].status = "succeeded";
    useTutorStoreMock.mockImplementation(
      (selector: (value: unknown) => unknown) =>
        selector({
          userId: "u1",
          sessionId: "sess-1",
          jobsById: {
            ...state.jobsById,
            ...terminal.jobsById,
          },
        }),
    );

    render(<JobTray />);

    expect(subscribe).toHaveBeenCalledWith("job-live", "resource_generation", {
      sessionId: "sess-1",
    });
    expect(subscribe).toHaveBeenCalledTimes(1);
    expect(subscribe).not.toHaveBeenCalledWith(
      "job-done",
      expect.anything(),
      expect.anything(),
    );
  });
});
