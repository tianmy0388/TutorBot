import { beforeEach, describe, expect, it } from "vitest";

import {
  DETAIL_WIDTH_DEFAULT,
  DETAIL_WIDTH_MAX,
  DETAIL_WIDTH_MIN,
  DETAIL_WIDTH_STORAGE_KEY,
  clampDetailWidth,
  readDetailWidth,
  writeDetailWidth,
} from "./detail-pane-width";

describe("detail pane width", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("clamps into [320, 760]", () => {
    expect(clampDetailWidth(100)).toBe(DETAIL_WIDTH_MIN);
    expect(clampDetailWidth(5000)).toBe(DETAIL_WIDTH_MAX);
    expect(clampDetailWidth(520)).toBe(520);
  });

  it("reads the default when nothing is stored", () => {
    expect(readDetailWidth()).toBe(DETAIL_WIDTH_DEFAULT);
  });

  it("round-trips through localStorage with clamping", () => {
    writeDetailWidth(640);
    expect(readDetailWidth()).toBe(640);
    writeDetailWidth(9999);
    expect(readDetailWidth()).toBe(DETAIL_WIDTH_MAX);
  });

  it("falls back to the default for corrupt stored values", () => {
    window.localStorage.setItem(DETAIL_WIDTH_STORAGE_KEY, "not-a-number");
    expect(readDetailWidth()).toBe(DETAIL_WIDTH_DEFAULT);
  });
});
