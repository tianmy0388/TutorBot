import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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

const api = vi.hoisted(() => ({
  getExerciseResponseState: vi.fn(),
  putExerciseDraft: vi.fn(),
  submitExerciseResponse: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

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
          options: [],
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

beforeEach(() => {
  storeState.latestPackage = null;
  api.getExerciseResponseState.mockReset().mockResolvedValue({ draft: null, submissions: [] });
  api.putExerciseDraft.mockReset().mockResolvedValue({});
  api.submitExerciseResponse.mockReset().mockImplementation(
    (_packageId: string, _resourceId: string, questionId: string, payload: { answer_json: unknown }) =>
      Promise.resolve({
        submission_id: `submission-${questionId}`,
        question_id: questionId,
        answer_json: payload.answer_json,
        answer: true,
        explanation: "yes",
        grading_status: "auto_graded",
        correct: payload.answer_json === true,
        score: payload.answer_json === true ? 1 : 0,
      }),
  );
});

afterEach(() => cleanup());

function selectCurrentPackage(resource: Resource) {
  storeState.latestPackage = {
    package_id: String(resource.metadata.package_id),
    resources: [resource],
  };
}

describe("ExerciseViewer code integration", () => {
  it("restores an unsubmitted choice draft after remount", async () => {
    const resource = exerciseResource();
    resource.format_specific.questions = [{
      id: "q1", type: "single_choice", question: "选择",
      options: [{ label: "A", text: "甲" }, { label: "B", text: "乙" }],
    }];
    api.getExerciseResponseState.mockResolvedValue({
      draft: { question_id: "q1", answer_json: "B" }, submissions: [],
    });
    const first = render(<ExerciseViewer resource={resource} />);
    expect(await screen.findByDisplayValue("B")).toBeChecked();
    first.unmount();
    render(<ExerciseViewer resource={resource} />);
    expect(await screen.findByDisplayValue("B")).toBeChecked();
  });

  it("submits only after an explicit action and restores server feedback", async () => {
    const resource = exerciseResource();
    // Projected resources never carry answer/explanation; restored
    // submissions do — post-submit feedback must come from the submission.
    resource.format_specific.questions = [{
      id: "q1", type: "true_false", question: "Python 是语言",
      options: [],
    }];
    api.getExerciseResponseState.mockResolvedValue({
      draft: null,
      submissions: [{
        submission_id: "old", question_id: "q1", answer_json: true,
        answer: true, explanation: "yes",
        grading_status: "auto_graded", correct: true, score: 1,
      }],
    });
    render(<ExerciseViewer resource={resource} />);
    expect(await screen.findByText("解析：")).toBeVisible();
    expect(screen.getByRole("button", { name: "✓ 正确" })).toHaveClass("border-green-500");
    expect(api.submitExerciseResponse).not.toHaveBeenCalled();
  });

  it("renders duplicate and empty legacy options without duplicate-key warnings", () => {
    const resource = exerciseResource();
    resource.format_specific.questions = [
      {
        id: "q-options",
        type: "single_choice",
        question: "选择答案",
        options: [
          { label: "A:0", text: "第一个" },
          { label: "A", text: "第二个" },
          { label: "A", text: "" },
        ],
      },
    ];
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    render(<ExerciseViewer resource={resource} />);

    expect(screen.getByText("第一个")).toBeVisible();
    expect(screen.getByText("第二个")).toBeVisible();
    expect(errorSpy).not.toHaveBeenCalledWith(
      expect.stringContaining("Encountered two children with the same key"),
    );
    errorSpy.mockRestore();
  });

  it("uses server submission feedback while excluding code from the ordinary score", async () => {
    const resource = exerciseResource();
    selectCurrentPackage(resource);
    render(<ExerciseViewer resource={resource} />);
    expect(screen.getByText("0").parentElement).toHaveTextContent("0 / 1");
    expect(screen.getByTestId("code-editor")).toHaveTextContent("pkg-owned");

    fireEvent.click(screen.getByRole("button", { name: "✓ 正确" }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));
    await waitFor(() => expect(screen.getByText("1").parentElement).toHaveTextContent("1 / 1"));
    expect(await screen.findByText("全部答对，做得很好。")).toBeVisible();
    expect(await screen.findByText("解析：")).toBeVisible();
    expect(screen.getByTestId("code-editor")).toBeVisible();
  });

  it("reflects an ordinary submission in progress and prevents a duplicate click", async () => {
    let resolve!: (value: unknown) => void;
    api.submitExerciseResponse.mockReturnValue(new Promise((done) => { resolve = done; }));
    const resource = exerciseResource();
    resource.format_specific.questions = [{
      id: "q1", type: "true_false", question: "Python 是语言",
      options: [],
    }];
    render(<ExerciseViewer resource={resource} />);
    fireEvent.click(screen.getByRole("button", { name: "✓ 正确" }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));
    expect(screen.getByRole("button", { name: "提交中…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "提交中…" }));
    expect(api.submitExerciseResponse).toHaveBeenCalledTimes(1);
    resolve({
      submission_id: "done", question_id: "q1", answer_json: true,
      answer: true, explanation: "yes",
      grading_status: "auto_graded", correct: true, score: 1,
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: "提交中…" })).not.toBeInTheDocument());
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

  it("submits a fill_blank array answer and renders per-blank submission feedback", async () => {
    const resource = exerciseResource();
    // Projected fill_blank question: no answer/explanation on the resource.
    resource.format_specific.questions = [{
      id: "q-fill",
      type: "fill_blank",
      question: "The capital of France is ___.",
      options: [],
    }];
    api.submitExerciseResponse.mockImplementation(
      (_packageId: string, _resourceId: string, questionId: string, payload: { answer_json: unknown }) =>
        Promise.resolve({
          submission_id: `submission-${questionId}`,
          question_id: questionId,
          answer_json: payload.answer_json,
          answer: "Paris",
          explanation: "法国首都是巴黎。",
          grading_status: "auto_graded",
          correct: true,
          score: 1,
        }),
    );
    render(<ExerciseViewer resource={resource} />);
    fireEvent.change(screen.getByPlaceholderText("…"), { target: { value: "Paris" } });
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));
    await waitFor(() =>
      expect(api.submitExerciseResponse).toHaveBeenCalledWith(
        "pkg-owned",
        "exercise-resource",
        "q-fill",
        expect.objectContaining({ answer_json: ["Paris"] }),
      ),
    );
    expect(await screen.findByText("解析：")).toBeVisible();
    expect(screen.getByText("法国首都是巴黎。")).toBeVisible();
    const slot = await screen.findByText("Paris");
    expect(slot).toHaveClass("text-green-800");
  });

  it("surfaces a failed submission instead of swallowing it", async () => {
    api.submitExerciseResponse.mockRejectedValue({ status: 500, detail: "backend exploded" });
    const resource = exerciseResource();
    resource.format_specific.questions = [{
      id: "q1", type: "true_false", question: "Python 是语言",
      options: [],
    }];
    render(<ExerciseViewer resource={resource} />);
    fireEvent.click(screen.getByRole("button", { name: "✓ 正确" }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));
    expect(await screen.findByText("backend exploded")).toBeVisible();
  });

  it("keeps conflicted submission retries silent", async () => {
    api.submitExerciseResponse.mockRejectedValue({ status: 409, detail: "submission conflict" });
    const resource = exerciseResource();
    resource.format_specific.questions = [{
      id: "q1", type: "true_false", question: "Python 是语言",
      options: [],
    }];
    render(<ExerciseViewer resource={resource} />);
    fireEvent.click(screen.getByRole("button", { name: "✓ 正确" }));
    fireEvent.click(screen.getByRole("button", { name: /^提交$/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^提交$/ })).toBeEnabled(),
    );
    expect(api.submitExerciseResponse).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("submission conflict")).not.toBeInTheDocument();
  });
});
