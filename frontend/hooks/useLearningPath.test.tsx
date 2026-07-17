import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { getLearningPath } from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import { useLearningPath } from "./useLearningPath";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getLearningPath: vi.fn() };
});

const api = vi.mocked(getLearningPath);
const path = (path_id: string, profile_version = 2) => ({
  path_id,
  course: "course",
  name: `Path ${path_id}`,
  description: "",
  nodes: [],
  total_estimated_hours: 0,
  completed_count: 0,
  available_count: 0,
  locked_count: 0,
  generated_at: new Date().toISOString(),
  profile_version,
});
beforeEach(() => {
  api.mockReset();
  useTutorStore.setState({
    userId: "local-user",
    plannedPath: null,
    plannedPathOwnerId: null,
    plannedPathLoaded: false,
    profile: null,
    profileOwnerId: null,
    profileLoaded: false,
  } as any);
});
afterEach(cleanup);

it("settles an absent persisted path to empty", async () => {
  api.mockResolvedValueOnce(null);
  const { result } = renderHook(() => useLearningPath());
  expect(result.current.status).toBe("loading");
  await waitFor(() => expect(result.current.status).toBe("empty"));
});

it("settles a failed request and clears loading", async () => {
  api.mockRejectedValueOnce(new Error("offline"));
  const { result } = renderHook(() => useLearningPath());
  await waitFor(() => expect(result.current.status).toBe("failed"));
  expect(result.current.loading).toBe(false);
});

it("hides a completed user's cached path immediately when the user changes", async () => {
  let resolveB!: (value: null) => void;
  api
    .mockResolvedValueOnce(path("a"))
    .mockImplementationOnce(() => new Promise((resolve) => { resolveB = resolve; }));
  const { result, rerender } = renderHook(
    ({ userId }) => useLearningPath(userId),
    { initialProps: { userId: "a" } },
  );
  await waitFor(() => expect(result.current.status).toBe("success"));

  rerender({ userId: "b" });

  expect(result.current.path).toBeNull();
  expect(result.current.status).toBe("loading");
  expect(api).toHaveBeenLastCalledWith("b");
  await act(async () => resolveB(null));
  await waitFor(() => expect(result.current.status).toBe("empty"));
});

it("reports loading and then failed when refreshing a cached path fails", async () => {
  let rejectRefresh!: (reason: Error) => void;
  api
    .mockResolvedValueOnce(path("cached"))
    .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRefresh = reject; }));
  const { result } = renderHook(() => useLearningPath());
  await waitFor(() => expect(result.current.status).toBe("success"));

  act(() => { void result.current.refresh(); });

  expect(result.current.status).toBe("loading");
  await act(async () => rejectRefresh(new Error("refresh offline")));
  await waitFor(() => expect(result.current.status).toBe("failed"));
  expect(result.current.loading).toBe(false);
  expect(result.current.error).toBe("refresh offline");
});

it("marks a path stale when it predates the current profile", async () => {
  useTutorStore.setState({
    profile: { version: 3 } as any,
    profileOwnerId: "local-user",
    profileLoaded: true,
  } as any);
  api.mockResolvedValueOnce(path("old", 2));

  const { result } = renderHook(() => useLearningPath());

  await waitFor(() => expect(result.current.status).toBe("stale"));
  expect(result.current.stale).toBe(true);
});
