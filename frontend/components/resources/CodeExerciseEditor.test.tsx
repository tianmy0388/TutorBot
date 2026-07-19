import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { CodeExerciseQuestion, ExerciseAttempt } from "@/lib/types";
import { CodeExerciseEditor } from "./CodeExerciseEditor";

const api = vi.hoisted(() => ({
  listExerciseAttempts: vi.fn(),
  submitExerciseAttempt: vi.fn(),
  getExerciseResponseState: vi.fn(),
  putExerciseDraft: vi.fn(),
  submitExerciseResponse: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

const question: CodeExerciseQuestion = {
  id: "q-code",
  type: "code",
  difficulty: 2,
  knowledge_point: "addition",
  question: "实现 add",
  options: [],
  explanation: "",
  code_spec: {
    language: "python",
    starter_code: "def add(a, b):\n    pass",
    time_limit_seconds: 5,
    test_count: 2,
  },
};

function attempt(overrides: Partial<ExerciseAttempt> = {}): ExerciseAttempt {
  return {
    attempt_id: "attempt-1",
    client_attempt_id: "client-1",
    user_id: "local-user",
    session_id: "sess-code",
    package_id: "pkg-code",
    question_id: "q-code",
    source_code: "def add(a, b): return a + b",
    status: "passed",
    passed_tests: 2,
    total_tests: 2,
    test_results: [
      { name: "positive", passed: true, actual_json: 3 },
      { name: "negative", passed: true, actual_json: 0 },
    ],
    stdout: "done",
    stderr: "",
    duration_seconds: 0.12,
    created_at: "2026-07-18T00:00:00Z",
    error_code: null,
    ...overrides,
  };
}

function renderEditor(overrides: Record<string, unknown> = {}) {
  return render(
    <CodeExerciseEditor
      question={question}
      packageId="pkg-code"
      resourceId="resource-code"
      userId="local-user"
      sessionId="sess-code"
      {...overrides}
    />,
  );
}

beforeEach(() => {
  api.listExerciseAttempts.mockReset().mockResolvedValue({
    items: [], total: 0, limit: 20, offset: 0,
  });
  api.submitExerciseAttempt.mockReset().mockResolvedValue(attempt());
  api.getExerciseResponseState.mockReset().mockResolvedValue({ draft: null, submissions: [] });
  api.putExerciseDraft.mockReset().mockResolvedValue({});
  api.submitExerciseResponse.mockReset().mockResolvedValue({
    submission_id: "response-code", question_id: "q-code", answer_json: null,
    grading_status: "auto_graded", correct: true, score: 1,
  });
});

afterEach(() => cleanup());

describe("CodeExerciseEditor", () => {
  it("restores a persisted source draft without replacing execution history", async () => {
    api.getExerciseResponseState.mockResolvedValue({
      draft: { question_id: "q-code", answer_json: "def add(a, b): return a + b" },
      submissions: [],
    });
    api.listExerciseAttempts.mockResolvedValue({
      items: [attempt({ attempt_id: "older" })], total: 1, limit: 20, offset: 0,
    });
    renderEditor();
    expect(await screen.findByRole("textbox", { name: "Python 代码" })).toHaveValue("def add(a, b): return a + b");
    expect(await screen.findByText("older")).toBeVisible();
  });

  it("renders starter code, restores history, submits and remains editable", async () => {
    api.listExerciseAttempts.mockResolvedValue({
      items: [attempt({ attempt_id: "old", status: "failed", passed_tests: 1 })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    renderEditor();

    const editor = screen.getByRole("textbox", { name: "Python 代码" });
    expect(editor).toHaveValue("def add(a, b):\n    pass");
    expect(await screen.findByText("历史尝试")).toBeVisible();
    expect(screen.getByText("1 / 2")).toBeVisible();

    fireEvent.change(editor, { target: { value: "def add(a, b): return a + b" } });
    fireEvent.click(screen.getByRole("button", { name: "运行并提交" }));
    const result = await screen.findByRole("region", { name: "本次运行结果" });
    expect(within(result).getByText("全部测试通过")).toBeVisible();
    expect(screen.getByText("positive")).toBeVisible();
    expect(screen.getByText("done")).toBeVisible();
    expect(api.submitExerciseAttempt).toHaveBeenCalledWith(
      "pkg-code",
      "q-code",
      expect.objectContaining({ source_code: "def add(a, b): return a + b" }),
      expect.anything(),
    );

    fireEvent.change(editor, { target: { value: "def add(a, b): return 0" } });
    expect(editor).toHaveValue("def add(a, b): return 0");
  });

  it.each([
    ["failed", "部分测试未通过"],
    ["timeout", "运行超时"],
    ["policy_rejected", "代码不符合本地执行策略"],
  ] as const)("renders terminal %s results", async (status, label) => {
    api.submitExerciseAttempt.mockResolvedValue(
      attempt({ status, passed_tests: 0, error_code: "SAFE_CODE" }),
    );
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "运行并提交" }));
    const result = await screen.findByRole("region", { name: "本次运行结果" });
    expect(within(result).getByText(label)).toBeVisible();
  });

  it("prevents double submit while the request is running", async () => {
    let resolve!: (value: ExerciseAttempt) => void;
    api.submitExerciseAttempt.mockReturnValue(
      new Promise<ExerciseAttempt>((done) => { resolve = done; }),
    );
    renderEditor();
    const button = screen.getByRole("button", { name: "运行并提交" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(screen.getByRole("button", { name: "运行中…" })).toBeDisabled();
    expect(api.submitExerciseAttempt).toHaveBeenCalledTimes(1);
    resolve(attempt());
    const result = await screen.findByRole("region", { name: "本次运行结果" });
    expect(within(result).getByText("全部测试通过")).toBeVisible();
  });

  it("keeps the latest draft when an in-flight execution resolves", async () => {
    let resolveRun!: (value: ExerciseAttempt) => void;
    api.submitExerciseAttempt.mockReturnValue(new Promise<ExerciseAttempt>((resolve) => { resolveRun = resolve; }));
    renderEditor();
    const editor = screen.getByRole("textbox", { name: "Python 代码" });
    fireEvent.change(editor, { target: { value: "executed source" } });
    fireEvent.click(screen.getByRole("button", { name: "运行并提交" }));
    fireEvent.change(editor, { target: { value: "newer draft" } });
    resolveRun(attempt({ source_code: "executed source" }));

    expect(await screen.findByRole("region", { name: "本次运行结果" })).toBeVisible();
    await waitFor(() => expect(editor).toHaveValue("newer draft"));
    expect(api.submitExerciseResponse).toHaveBeenCalledWith(
      "pkg-code", "resource-code", "q-code",
      expect.objectContaining({ answer_json: "newer draft" }),
    );
    expect(screen.getByText("attempt-1")).toBeVisible();
  });

  it("accepts only bounded .py uploads and preserves source on file errors", async () => {
    renderEditor();
    const editor = screen.getByRole("textbox", { name: "Python 代码" });
    const input = screen.getByLabelText("上传 Python 文件");

    fireEvent.change(input, {
      target: { files: [new File(["print(1)"], "answer.py", { type: "text/x-python" })] },
    });
    await waitFor(() => expect(editor).toHaveValue("print(1)"));

    fireEvent.change(input, {
      target: { files: [new File(["bad"], "answer.txt", { type: "text/plain" })] },
    });
    expect(await screen.findByText("只能上传 .py 文件")).toBeVisible();
    expect(editor).toHaveValue("print(1)");

    fireEvent.change(input, {
      target: { files: [new File(["x".repeat(128 * 1024 + 1)], "large.py")] },
    });
    expect(await screen.findByText("文件不能超过 128 KiB")).toBeVisible();
    expect(editor).toHaveValue("print(1)");

    const unreadable = new File(["secret"], "broken.py");
    Object.defineProperty(unreadable, "text", {
      value: () => Promise.reject(new Error("private read error")),
    });
    fireEvent.change(input, { target: { files: [unreadable] } });
    expect(await screen.findByText("无法读取 Python 文件")).toBeVisible();
    expect(editor).toHaveValue("print(1)");
  });

  it("fences stale history and submit responses after identity changes", async () => {
    let resolveOldHistory!: (value: unknown) => void;
    let resolveOldSubmit!: (value: ExerciseAttempt) => void;
    api.listExerciseAttempts
      .mockReturnValueOnce(new Promise((done) => { resolveOldHistory = done; }))
      .mockResolvedValueOnce({
        items: [attempt({ attempt_id: "new-history", question_id: "q-new" })],
        total: 1, limit: 20, offset: 0,
      });
    api.submitExerciseAttempt.mockReturnValue(
      new Promise<ExerciseAttempt>((done) => { resolveOldSubmit = done; }),
    );
    const view = renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "运行并提交" }));

    const nextQuestion = { ...question, id: "q-new", question: "新题目" };
    view.rerender(
      <CodeExerciseEditor
        question={nextQuestion}
        packageId="pkg-new"
        resourceId="resource-new"
        userId="new-user"
        sessionId="sess-new"
      />,
    );
    expect(await screen.findByText("历史尝试")).toBeVisible();
    resolveOldHistory({ items: [attempt({ attempt_id: "stale-history" })], total: 1 });
    resolveOldSubmit(attempt({ attempt_id: "stale-submit" }));
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "本次运行结果" })).not.toBeInTheDocument();
      expect(screen.queryByText("stale-history")).not.toBeInTheDocument();
    });
  });

  it("shows disabled states for missing package or code spec", async () => {
    const noPackage = renderEditor({ packageId: null });
    expect(screen.getByText("该资源尚未持久化，暂不能提交代码。")).toBeVisible();
    expect(screen.getByRole("button", { name: "运行并提交" })).toBeDisabled();
    noPackage.unmount();

    renderEditor({ question: { ...question, code_spec: null } });
    expect(screen.getByText("题目执行配置不可用。")).toBeVisible();
    expect(screen.getByRole("button", { name: "运行并提交" })).toBeDisabled();
  });
});
