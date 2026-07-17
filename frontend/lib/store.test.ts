import { beforeEach, describe, expect, it } from "vitest";

import { getOrCreateUserId } from "./store";

describe("getOrCreateUserId", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns the canonical identity in local mode", () => {
    localStorage.setItem("tutor-user-id", "u_stale");
    localStorage.setItem("tutor:user_id", "u_legacy");

    expect(getOrCreateUserId(false)).toBe("local-user");
    expect(localStorage.getItem("tutor-user-id")).toBe("local-user");
    expect(localStorage.getItem("tutor:user_id")).toBeNull();
  });

  it("retains an explicit identity in multi-user mode", () => {
    localStorage.setItem("tutor-user-id", "u_alice");

    expect(getOrCreateUserId(true)).toBe("u_alice");
  });

  it("migrates a legacy identity in multi-user mode", () => {
    localStorage.setItem("tutor:user_id", "u_legacy");

    expect(getOrCreateUserId(true)).toBe("u_legacy");
    expect(localStorage.getItem("tutor-user-id")).toBe("u_legacy");
    expect(localStorage.getItem("tutor:user_id")).toBeNull();
  });

  it("generates a new identity for a blank legacy value", () => {
    localStorage.setItem("tutor:user_id", "   ");

    const identity = getOrCreateUserId(true);

    expect(identity).toMatch(/^u_[a-zA-Z0-9_]+$/);
    expect(localStorage.getItem("tutor-user-id")).toBe(identity);
    expect(localStorage.getItem("tutor:user_id")).toBeNull();
  });
});
