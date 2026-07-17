import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { getLearningPath } from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import { useLearningPath } from "./useLearningPath";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getLearningPath: vi.fn() };
});

const api = vi.mocked(getLearningPath);
beforeEach(() => {
  api.mockReset();
  useTutorStore.setState({ userId: "local-user", plannedPath: null });
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
