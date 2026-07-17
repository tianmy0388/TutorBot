import { beforeEach, describe, expect, it } from "vitest";

import { getOrCreateUserId } from "./store";

describe("getOrCreateUserId", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns the canonical identity in local mode", () => {
    localStorage.setItem("tutor-user-id", "u_stale");

    expect(getOrCreateUserId(false)).toBe("local-user");
    expect(localStorage.getItem("tutor-user-id")).toBe("local-user");
  });

  it("retains an explicit identity in multi-user mode", () => {
    localStorage.setItem("tutor-user-id", "u_alice");

    expect(getOrCreateUserId(true)).toBe("u_alice");
  });
});
