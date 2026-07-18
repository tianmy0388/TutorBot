import { describe, expect, it } from "vitest";

import {
  isUsableResourcePackage,
  isUsableStreamedResource,
} from "./resource-validation";

function exerciseWithOptions(options: unknown[]) {
  return {
    resource_id: "exercise-1",
    type: "exercise",
    title: "练习",
    content: "",
    format_specific: {
      questions: [{ id: "q-1", options }],
    },
    difficulty: 2,
    estimated_minutes: 5,
    prerequisites: [],
    generated_by: [],
    confidence_score: 0.8,
    topic: "math",
    tags: [],
    created_at: "2026-07-19T00:00:00Z",
    metadata: {},
  };
}

describe("resource stream validation", () => {
  it("rejects redacted or string exercise options", () => {
    expect(isUsableStreamedResource(exerciseWithOptions(["[TRUNCATED]"]))).toBe(false);
    expect(
      isUsableStreamedResource(
        exerciseWithOptions([{ label: "", text: "" }]),
      ),
    ).toBe(false);
  });

  it("accepts non-exercise resources without exercise-only fields", () => {
    expect(
      isUsableStreamedResource({
        resource_id: "video-1",
        type: "video",
        title: "动画",
        content: "",
        format_specific: { render_status: "pending" },
      }),
    ).toBe(true);
  });

  it("requires every package resource to be usable", () => {
    expect(
      isUsableResourcePackage({
        package_id: "pkg-1",
        resources: [exerciseWithOptions(["[TRUNCATED]"])],
      }),
    ).toBe(false);
  });
});
