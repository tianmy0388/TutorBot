"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileCode2, Play } from "lucide-react";

import { listExerciseAttempts, submitExerciseAttempt } from "@/lib/api";
import { useExerciseResponses } from "@/hooks/useExerciseResponses";
import type {
  CodeExerciseQuestion,
  ExerciseAttempt,
  ExerciseAttemptStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const MAX_SOURCE_BYTES = 128 * 1024;

export interface CodeExerciseEditorProps {
  question: CodeExerciseQuestion;
  packageId: string | null;
  resourceId: string;
  userId: string;
  sessionId: string;
}

export function CodeExerciseEditor({
  question,
  packageId,
  resourceId,
  userId,
  sessionId,
}: CodeExerciseEditorProps) {
  const spec = question.code_spec;
  const identity = `${userId}\u0000${sessionId}\u0000${packageId ?? ""}\u0000${resourceId}\u0000${question.id}`;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const mountedRef = useRef(true);
  const runningRef = useRef(false);
  const [history, setHistory] = useState<ExerciseAttempt[]>([]);
  const [result, setResult] = useState<ExerciseAttempt | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const responses = useExerciseResponses({
    userId,
    packageId,
    resourceId,
    sessionId,
  }, [question.id]);
  const draft = responses.drafts[question.id];
  const source = typeof draft === "string" ? draft : (spec?.starter_code ?? "");

  useEffect(() => {
    mountedRef.current = true;
    runningRef.current = false;
    setHistory([]);
    setResult(null);
    setRunning(false);
    setError("");
    setUploadError("");
    if (!packageId || !spec || isPendingPackage(packageId)) {
      return () => { mountedRef.current = false; };
    }
    const controller = new AbortController();
    const requestIdentity = identity;
    listExerciseAttempts(packageId, question.id, userId, controller.signal)
      .then((response) => {
        if (mountedRef.current && identityRef.current === requestIdentity) {
          setHistory(response.items);
        }
      })
      .catch((reason: unknown) => {
        if (
          mountedRef.current &&
          identityRef.current === requestIdentity &&
          !isAbortError(reason)
        ) {
          setError("历史记录加载失败");
        }
      });
    return () => {
      controller.abort();
      mountedRef.current = false;
    };
  }, [identity, packageId, question.id, spec, userId]);

  const disabledPackage = !packageId || isPendingPackage(packageId);
  const submit = async () => {
    if (runningRef.current || !spec || disabledPackage || !packageId) return;
    if (!source.trim()) {
      setError("请输入 Python 代码");
      return;
    }
    if (new TextEncoder().encode(source).byteLength > MAX_SOURCE_BYTES) {
      setError("代码不能超过 128 KiB");
      return;
    }
    runningRef.current = true;
    setRunning(true);
    setError("");
    const requestIdentity = identityRef.current;
    const controller = new AbortController();
    try {
      const terminal = await submitExerciseAttempt(
        packageId,
        question.id,
        {
          user_id: userId,
          session_id: sessionId,
          source_code: source,
          client_attempt_id: createClientAttemptId(),
        },
        controller.signal,
      );
      if (mountedRef.current && identityRef.current === requestIdentity) {
        setResult(terminal);
        setHistory((items) => [terminal, ...items.filter((item) => item.attempt_id !== terminal.attempt_id)]);
        void responses.submit(question.id, {
          answer: source,
          linkedCodeAttemptId: terminal.attempt_id,
          keepDraft: true,
        });
      }
    } catch (reason) {
      if (
        mountedRef.current &&
        identityRef.current === requestIdentity &&
        !isAbortError(reason)
      ) {
        setError(publicErrorMessage(reason));
      }
    } finally {
      if (mountedRef.current && identityRef.current === requestIdentity) {
        runningRef.current = false;
        setRunning(false);
      }
    }
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setUploadError("");
    if (!file.name.toLowerCase().endsWith(".py")) {
      setUploadError("只能上传 .py 文件");
      return;
    }
    if (file.size > MAX_SOURCE_BYTES) {
      setUploadError("文件不能超过 128 KiB");
      return;
    }
    const requestIdentity = identityRef.current;
    try {
      const text = await readFileText(file);
      if (new TextEncoder().encode(text).byteLength > MAX_SOURCE_BYTES) {
        setUploadError("文件不能超过 128 KiB");
        return;
      }
      if (mountedRef.current && identityRef.current === requestIdentity) {
        responses.setDraft(question.id, text);
      }
    } catch {
      if (mountedRef.current && identityRef.current === requestIdentity) {
        setUploadError("无法读取 Python 文件");
      }
    }
  };

  return (
    <div className="mt-3 space-y-3 rounded-lg border border-fg/10 bg-bg-panel/40 p-3">
      <label className="block text-xs font-medium text-fg-muted" htmlFor={`code-${question.id}`}>
        Python 代码
      </label>
      <textarea
        id={`code-${question.id}`}
        aria-label="Python 代码"
        value={source}
        onChange={(event) => responses.setDraft(question.id, event.target.value)}
        spellCheck={false}
        className="min-h-52 w-full resize-y rounded-md border border-fg/10 bg-black/50 p-3 font-mono text-xs leading-5 text-fg focus:border-brand-500 focus:outline-none"
      />
      <div className="flex flex-wrap items-center gap-2">
        <label className="btn-ghost cursor-pointer px-3 py-1.5 text-xs">
          <FileCode2 className="h-3.5 w-3.5" />
          上传 Python 文件
          <input
            className="sr-only"
            aria-label="上传 Python 文件"
            type="file"
            accept=".py,text/x-python"
            onChange={(event) => {
              void upload(event.target.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
        </label>
        <button
          type="button"
          onClick={() => { void submit(); }}
          disabled={running || !spec || disabledPackage}
          className={cn(
            "btn-primary px-3 py-1.5 text-xs",
            (running || !spec || disabledPackage) && "cursor-not-allowed opacity-50",
          )}
        >
          <Play className="h-3.5 w-3.5" />
          {running ? "运行中…" : "运行并提交"}
        </button>
        {spec && (
          <span className="text-[10px] text-fg-subtle">
            {spec.test_count} 项测试 · {spec.time_limit_seconds}s
          </span>
        )}
      </div>

      {disabledPackage && <Notice text="该资源尚未持久化，暂不能提交代码。" />}
      {!spec && <Notice text="题目执行配置不可用。" />}
      {uploadError && <Notice text={uploadError} error />}
      {error && <Notice text={error} error />}
      {result && <AttemptResult attempt={result} />}
      {history.length > 0 && (
        <section aria-label="历史尝试" className="space-y-1.5 border-t border-fg/10 pt-3">
          <h4 className="text-xs font-semibold text-fg-muted">历史尝试</h4>
          {history.map((item) => (
            <div key={item.attempt_id} className="flex items-center justify-between rounded bg-bg-card px-2 py-1 text-[11px]">
              <code className="text-fg-subtle">{item.attempt_id}</code>
              <span>{item.passed_tests} / {item.total_tests}</span>
              <span>{statusLabel(item.status)}</span>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

function AttemptResult({ attempt }: { attempt: ExerciseAttempt }) {
  return (
    <section aria-label="本次运行结果" className="space-y-2 rounded-md border border-fg/10 bg-bg-card p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        {attempt.status === "passed" ? (
          <CheckCircle2 className="h-4 w-4 text-green-400" />
        ) : (
          <AlertCircle className="h-4 w-4 text-amber-400" />
        )}
        {terminalMessage(attempt.status)}
        <span className="ml-auto text-xs text-fg-muted">
          {attempt.passed_tests} / {attempt.total_tests}
        </span>
      </div>
      {attempt.test_results.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead><tr className="text-fg-subtle"><th>测试</th><th>结果</th><th>实际输出</th></tr></thead>
          <tbody>
            {attempt.test_results.map((test, index) => (
              <tr key={`${test.name}-${index}`} className="border-t border-fg/5">
                <td className="py-1">{test.name}</td>
                <td>{test.passed ? "通过" : "未通过"}</td>
                <td className="font-mono text-[10px]">{formatActual(test.actual_json, test.error_code)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {attempt.stdout && <pre className="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-black/50 p-2 text-xs text-green-300">{attempt.stdout}</pre>}
      {attempt.stderr && <pre className="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-black/50 p-2 text-xs text-red-300">{attempt.stderr}</pre>}
    </section>
  );
}

function Notice({ text, error = false }: { text: string; error?: boolean }) {
  return <div className={cn("text-xs text-amber-300", error && "text-red-300")}>{text}</div>;
}

function terminalMessage(status: ExerciseAttemptStatus) {
  return {
    passed: "全部测试通过",
    failed: "部分测试未通过",
    syntax_error: "代码存在语法错误",
    timeout: "运行超时",
    policy_rejected: "代码不符合本地执行策略",
    error: "代码执行失败",
  }[status];
}

function statusLabel(status: ExerciseAttemptStatus) {
  return terminalMessage(status);
}

function formatActual(value: unknown, errorCode?: string | null) {
  if (errorCode) return errorCode;
  if (value === undefined) return "—";
  try { return JSON.stringify(value); } catch { return "—"; }
}

function isPendingPackage(packageId: string) {
  return packageId.startsWith("pending-") || packageId.startsWith("partial-");
}

function isAbortError(reason: unknown) {
  return (
    typeof reason === "object" &&
    reason !== null &&
    "name" in reason &&
    (reason as { name?: unknown }).name === "AbortError"
  );
}

function publicErrorMessage(reason: unknown) {
  if (reason && typeof reason === "object" && "detail" in reason) {
    const detail = (reason as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return "代码提交失败，请稍后重试";
}

function createClientAttemptId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `attempt-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function readFileText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsText(file, "utf-8");
  });
}
