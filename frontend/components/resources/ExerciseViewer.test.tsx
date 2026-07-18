import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { Resource } from "@/lib/types";
import { ExerciseViewer } from "./ExerciseViewer";

const storeState = vi.hoisted(() => ({
  userId: "local-user",
  sessionId: "sess-current",
  latestPackage: null as {
    package_id: string;
    resources: Resource[];
  } | null,
}));

vi.mock("./CodeExerciseEditor", () => ({
  CodeExerciseEditor: ({ packageId }: { packageId: string | null }) => (
    <div data-testid="code-editor">code editor: {packageId ?? "disabled"}</div>
  ),
}));

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: unknown) => unknown) =>
    selector(storeState),
}));

function exerciseResource(
  packageId = "pkg-owned",
  packagePersisted = true,
): Resource {
  return {
    resource_id: "exercise-resource",
    type: "exercise",
    title: "Mixed exercises",
    content: "",
    format_specific: {
      questions: [
        {
          id: "q-bool",
          type: "true_false",
          question: "Python 是语言",
          answer: true,
          options: [],
          explanation: "yes",
        },
        {
          id: "q-code",
          type: "code",
          question: "实现 solve",
          options: [],
          code_spec: {
            language: "python",
            starter_code: "def solve(): pass",
            time_limit_seconds: 5,
            test_count: 1,
          },
        },
      ],
    },
    difficulty: 2,
    estimated_minutes: 5,
    prerequisites: [],
    generated_by: [],
    confidence_score: 0.8,
    topic: "python",
    tags: [],
    created_at: "2026-07-18T00:00:00Z",
    metadata: {
      package_id: packageId,
      package_persisted: packagePersisted,
    },
  };
}

afterEach(() => {
  cleanup();
  storeState.latestPackage = null;
});

function selectCurrentPackage(resource: Resource) {
  storeState.latestPackage = {
    package_id: String(resource.metadata.package_id),
    resources: [resource],
  };
}

describe("ExerciseViewer code integration", () => {
  it("keeps non-code scoring unchanged and excludes code from bulk local scoring", () => {
    const resource = exerciseResource();
    selectCurrentPackage(resource);
    render(<ExerciseViewer resource={resource} />);
    expect(screen.getByText("0").parentElement).toHaveTextContent("0 / 1");
    expect(screen.getByTestId("code-editor")).toHaveTextContent("pkg-owned");

    fireEvent.click(screen.getByRole("button", { name: "✓ 正确" }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));
    expect(screen.getByText("1").parentElement).toHaveTextContent("1 / 1");
    expect(screen.getByText("🎉 全对！太棒了！")).toBeVisible();
    expect(screen.getByTestId("code-editor")).toBeVisible();
  });

  it("disables code submission when package persistence failed", () => {
    const resource = exerciseResource("pkg-failed", false);
    selectCurrentPackage(resource);
    render(<ExerciseViewer resource={resource} />);
    expect(screen.getByTestId("code-editor")).toHaveTextContent("disabled");
  });

  it("enables code submission for a persisted package restored as current", () => {
    const restoredResource = exerciseResource("pkg-restored", true);
    selectCurrentPackage(restoredResource);
    render(<ExerciseViewer resource={restoredResource} />);
    expect(screen.getByTestId("code-editor")).toHaveTextContent("pkg-restored");
  });

  it("keeps a historical persisted package enabled when another package is current", () => {
    const resource = exerciseResource("pkg-owned", true);
    storeState.latestPackage = {
      package_id: "pkg-unrelated",
      resources: [{ ...resource, resource_id: "other-resource" }],
    };
    render(<ExerciseViewer resource={resource} />);
    expect(screen.getByTestId("code-editor")).toHaveTextContent("pkg-owned");
  });
});
