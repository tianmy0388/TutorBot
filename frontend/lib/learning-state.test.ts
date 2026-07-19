import { beforeEach, describe, expect, it, vi } from "vitest";

import { getLearningPath, getProfile } from "./api";
import { refreshLearningState } from "./learning-state";
import { useTutorStore } from "./store";

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    getProfile: vi.fn(),
    getLearningPath: vi.fn(),
  };
});

const mockedGetProfile = vi.mocked(getProfile);
const mockedGetLearningPath = vi.mocked(getLearningPath);

describe("refreshLearningState", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useTutorStore.setState({
      profile: null,
      profileOwnerId: null,
      profileLoaded: false,
      plannedPath: null,
      plannedPathOwnerId: null,
      plannedPathLoaded: false,
    });
  });

  it("hydrates the canonical persisted profile and path for one owner", async () => {
    const profile = { user_id: "local-user", version: 3 };
    const path = { path_id: "path-3", profile_version: 3 };
    mockedGetProfile.mockResolvedValue(profile as never);
    mockedGetLearningPath.mockResolvedValue(path as never);

    await refreshLearningState("local-user", "ai_introduction");

    expect(mockedGetProfile).toHaveBeenCalledWith("local-user");
    expect(mockedGetLearningPath).toHaveBeenCalledWith("local-user");
    expect(useTutorStore.getState()).toMatchObject({
      profile,
      profileOwnerId: "local-user",
      profileLoaded: true,
      plannedPath: path,
      plannedPathOwnerId: "local-user",
      plannedPathLoaded: true,
    });
  });

  it("settles missing durable state without creating a second path", async () => {
    mockedGetProfile.mockResolvedValue(null);
    mockedGetLearningPath.mockResolvedValue(null);

    await refreshLearningState("local-user", "ai_introduction");

    expect(useTutorStore.getState()).toMatchObject({
      profile: null,
      profileOwnerId: "local-user",
      profileLoaded: true,
      plannedPath: null,
      plannedPathOwnerId: "local-user",
      plannedPathLoaded: true,
    });
  });
});
