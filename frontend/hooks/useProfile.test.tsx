import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useProfile } from "./useProfile";
import { useTutorStore } from "@/lib/store";
import { getProfile } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getProfile: vi.fn() };
});

const mockedGetProfile = vi.mocked(getProfile);
const profile = (user_id: string) => ({
  user_id,
  version: 2,
  cognitive_style: "visual" as const,
  knowledge_count: 1,
  avg_mastery: 0.7,
  weak_concepts: [],
  strong_concepts: [],
  error_pattern_count: 0,
  goal: "curiosity" as const,
  urgency: "medium" as const,
  self_efficacy: 0.5,
  modality_dominant: "video",
  session_duration_min: 30,
  updated_at: new Date().toISOString(),
  knowledge_map: { attention: 0.7 },
  modality: { text: 0.5, video: 0.8, interactive: 0.5, diagram: 0.5, code: 0.5, audio: 0.2, exercise: 0.7 },
  pace: { avg_session_duration_min: 30, preferred_chunk_size_min: 15, review_interval_hours: 24, daily_time_budget_min: 60, sessions_per_week: 5 },
  motivation: { goal_type: "curiosity" as const, goal_description: "", urgency: "medium" as const, self_efficacy: 0.5, stakes: "" },
  error_patterns: [],
  metadata: {},
});

beforeEach(() => {
  mockedGetProfile.mockReset();
  useTutorStore.setState({ userId: "local-user", profile: null, profileLoaded: false });
});
afterEach(cleanup);

describe("useProfile states", () => {
  it("settles loading to empty", async () => {
    mockedGetProfile.mockResolvedValueOnce(null);
    const { result } = renderHook(() => useProfile());
    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current.status).toBe("empty"));
    expect(result.current.loading).toBe(false);
  });

  it("settles to success and failed without indefinite loading", async () => {
    mockedGetProfile.mockResolvedValueOnce(profile("local-user"));
    const success = renderHook(() => useProfile());
    await waitFor(() => expect(success.result.current.status).toBe("success"));
    success.unmount();
    useTutorStore.setState({ profile: null, profileLoaded: false });
    mockedGetProfile.mockRejectedValueOnce(new Error("offline"));
    const failed = renderHook(() => useProfile());
    await waitFor(() => expect(failed.result.current.status).toBe("failed"));
    expect(failed.result.current.error).toBe("offline");
  });

  it("ignores a stale response after the requested user changes", async () => {
    let resolveA!: (value: ReturnType<typeof profile>) => void;
    let resolveB!: (value: ReturnType<typeof profile>) => void;
    mockedGetProfile
      .mockImplementationOnce(() => new Promise((resolve) => { resolveA = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveB = resolve; }));
    const { result, rerender } = renderHook(
      ({ userId }) => useProfile(userId),
      { initialProps: { userId: "a" } },
    );
    rerender({ userId: "b" });
    await act(async () => resolveB(profile("b")));
    await waitFor(() => expect(result.current.profile?.user_id).toBe("b"));
    await act(async () => resolveA(profile("a")));
    expect(result.current.profile?.user_id).toBe("b");
  });
});
