import { afterEach, describe, expect, it } from "vitest";

import { resolveThemeColor } from "./theme-color";

describe("resolveThemeColor", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--color-test");
  });

  it("converts a space-separated RGB triplet to comma rgb()", () => {
    document.documentElement.style.setProperty("--color-test", "255 226 197");
    expect(resolveThemeColor("--color-test", "#000000")).toBe(
      "rgb(255,226,197)",
    );
  });

  it("returns the fallback when the variable is missing", () => {
    expect(resolveThemeColor("--color-does-not-exist", "#fffaf4")).toBe(
      "#fffaf4",
    );
  });

  it("returns the fallback for malformed values", () => {
    document.documentElement.style.setProperty("--color-test", "not-a-color");
    expect(resolveThemeColor("--color-test", "#000")).toBe("#000");
    document.documentElement.style.setProperty("--color-test", "255 226");
    expect(resolveThemeColor("--color-test", "#000")).toBe("#000");
  });
});
