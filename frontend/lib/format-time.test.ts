import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatRelativeTime } from "./format-time";

describe("formatRelativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-19T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders relative buckets", () => {
    expect(formatRelativeTime("2026-07-19T11:59:40Z")).toBe("刚刚");
    expect(formatRelativeTime("2026-07-19T11:30:00Z")).toBe("30 分钟前");
    expect(formatRelativeTime("2026-07-19T09:00:00Z")).toBe("3 小时前");
    expect(formatRelativeTime("2026-07-17T12:00:00Z")).toBe("2 天前");
    expect(formatRelativeTime("2026-07-01T12:00:00Z")).toBe("07-01");
  });

  it("handles invalid input", () => {
    expect(formatRelativeTime("not-a-date")).toBe("时间未知");
  });
});
