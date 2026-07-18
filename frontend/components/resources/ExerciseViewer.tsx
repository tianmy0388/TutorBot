"use client";

/**
 * ExerciseViewer — interactive quiz UI for exercise resources.
 *
 * Features:
 *  - Multiple question types (single_choice, multiple_choice, true_false, short_answer)
 *  - Per-question submit + reset
 *  - Score summary (correct / total)
 *  - Bulk submit + reset all
 *  - Filter by question type
 *  - Persistent state per-resource via key prop
 */

import { useState, useMemo } from "react";
import {
  Check,
  X,
  RotateCcw,
  Send,
  Filter,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTutorStore } from "@/lib/store";
import { useExerciseResponses } from "@/hooks/useExerciseResponses";
import type { CodeExerciseQuestion, PublicCodeSpec, Resource } from "@/lib/types";
import { CodeExerciseEditor } from "./CodeExerciseEditor";

interface ParsedQuestion {
  id: string;
  type: string;
  difficulty: number;
  knowledge_point: string;
  question: string;
  options: Array<{ label: string; text: string }>;
  answer?: string | string[] | boolean;
  explanation: string;
  code_spec: PublicCodeSpec | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cleanOptionText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function parseOptions(value: unknown): Array<{ label: string; text: string }> {
  if (!Array.isArray(value)) return [];
  return value.flatMap((option, index) => {
    if (!isRecord(option)) return [];
    const text = cleanOptionText(option.text);
    const label = cleanOptionText(option.label);
    if (
      !text ||
      text === "[TRUNCATED]" ||
      label === "[TRUNCATED]"
    ) {
      return [];
    }
    return [{
      label: label || String.fromCharCode(65 + index),
      text,
    }];
  });
}

function parseQuestions(resource: Resource): ParsedQuestion[] {
  const qs = (resource.format_specific?.questions as any[]) || [];
  return qs.map((q, i) => ({
    id: String(q.id ?? `q_${i}`),
    type: String(q.type || "single_choice"),
    difficulty: Number(q.difficulty || 2),
    knowledge_point: String(q.knowledge_point || ""),
    question: String(q.question || ""),
    options: parseOptions(q.options),
    answer: q.answer,
    explanation: String(q.explanation || ""),
    code_spec:
      q.code_spec && typeof q.code_spec === "object"
        ? (q.code_spec as PublicCodeSpec)
        : null,
  }));
}

const TYPE_LABELS: Record<string, string> = {
  single_choice: "单选",
  multiple_choice: "多选",
  true_false: "判断",
  fill_blank: "填空",
  short_answer: "简答",
  code: "代码",
};

export function ExerciseViewer({ resource }: { resource: Resource }) {
  const questions = useMemo(() => parseQuestions(resource), [resource]);
  const localQuestions = useMemo(
    () => questions.filter((question) => question.type !== "code"),
    [questions],
  );
  const userId = useTutorStore((state) => state.userId);
  const sessionId = useTutorStore((state) => state.sessionId);
  const latestPackage = useTutorStore((state) => state.latestPackage);
  const packageId = resolveDurablePackageId(resource, latestPackage);
  const [filter, setFilter] = useState<string>("all");
  const questionIds = useMemo(() => localQuestions.map((question) => question.id), [localQuestions]);
  const responses = useExerciseResponses({
    userId,
    packageId,
    resourceId: resource.resource_id,
    sessionId,
  }, questionIds);

  const filteredQuestions = useMemo(() => {
    if (filter === "all") return questions;
    return questions.filter((q) => q.type === filter);
  }, [questions, filter]);

  // Stats
  const stats = useMemo(() => {
    let correct = 0;
    let answered = 0;
    localQuestions.forEach((q) => {
      const submission = responses.submissions[q.id];
      if (submission) {
        answered += 1;
        if (submission.correct === true) correct += 1;
      }
    });
    return { correct, answered, total: localQuestions.length };
  }, [localQuestions, responses.submissions]);

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = { all: questions.length };
    questions.forEach((q) => {
      counts[q.type] = (counts[q.type] || 0) + 1;
    });
    return counts;
  }, [questions]);

  if (questions.length === 0) {
    return (
      <div className="text-sm text-fg-muted text-center py-8">
        该练习暂无题目。
      </div>
    );
  }

  const submitAll = () => {
    localQuestions.forEach((q) => {
      if (responses.drafts[q.id] !== undefined && !responses.submissions[q.id]) {
        void responses.submit(q.id);
      }
    });
  };

  const resetAll = () => {
    localQuestions.forEach((q) => responses.resetDraft(q.id));
  };

  const allSubmitted = stats.answered === stats.total && stats.total > 0;
  const hasSubmitting = localQuestions.some((question) => responses.submitting[question.id]);

  return (
    <div className="space-y-4">
      {/* Score summary */}
      <div className="p-4 bg-bg-card rounded-lg border border-fg/5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className="text-2xl font-bold">
              {stats.correct}
              <span className="text-fg-subtle text-base"> / {stats.total}</span>
            </div>
            <div className="text-xs text-fg-muted">
              已答 {stats.answered}/{stats.total}
              {stats.answered > 0 && (
                <span className="ml-2 text-accent">
                  ({((stats.correct / Math.max(1, stats.answered)) * 100).toFixed(0)}% 正确)
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={submitAll}
              disabled={stats.answered === stats.total || hasSubmitting}
              className={cn(
                "btn-ghost text-xs px-3 py-1.5",
                (stats.answered === stats.total || hasSubmitting) && "opacity-50 cursor-not-allowed",
              )}
            >
              <Send className="w-3 h-3" />
              全部提交
            </button>
            <button
              onClick={resetAll}
              disabled={stats.answered === 0}
              className={cn(
                "btn-ghost text-xs px-3 py-1.5",
                stats.answered === 0 && "opacity-50 cursor-not-allowed",
              )}
            >
              <RotateCcw className="w-3 h-3" />
              重置全部
            </button>
          </div>
        </div>

        {/* Progress bar */}
        <div className="h-1.5 bg-bg-panel rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-green-500 to-emerald-400 transition-all"
            style={{ width: `${(stats.correct / Math.max(1, stats.total)) * 100}%` }}
          />
        </div>

        {/* Type filter */}
        <div className="flex gap-1 mt-3 flex-wrap text-[10px]">
          <Filter className="w-3 h-3 text-fg-muted mt-1" />
          {Object.entries(typeCounts).map(([t, c]) => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={cn(
                "px-2 py-0.5 rounded-md transition-colors",
                filter === t
                  ? "bg-brand-600/30 text-brand-200"
                  : "text-fg-muted hover:text-fg bg-bg-panel",
              )}
            >
              {t === "all" ? "全部" : TYPE_LABELS[t] || t} ({c})
            </button>
          ))}
        </div>
      </div>

      {/* Final result banner */}
      {allSubmitted && (
        <div
          className={cn(
            "p-3 rounded-lg flex items-center gap-3 border",
            stats.correct === stats.total
              ? "bg-green-950/30 border-green-700/40"
              : stats.correct / stats.total >= 0.6
              ? "bg-yellow-950/30 border-yellow-700/40"
              : "bg-red-950/30 border-red-700/40",
          )}
        >
          {stats.correct === stats.total ? (
            <CheckCircle2 className="w-6 h-6 text-green-400 shrink-0" />
          ) : (
            <AlertCircle className="w-6 h-6 text-yellow-400 shrink-0" />
          )}
          <div className="flex-1 text-sm">
            {stats.correct === stats.total
              ? "🎉 全对！太棒了！"
              : stats.correct / stats.total >= 0.6
              ? `还不错，正确率 ${((stats.correct / stats.total) * 100).toFixed(0)}%`
              : `需要更多练习，正确率仅 ${((stats.correct / stats.total) * 100).toFixed(0)}%`}
          </div>
        </div>
      )}

      {/* Questions */}
      <div className="space-y-4">
        {filteredQuestions.map((q, idx) => {
          if (q.type === "code") {
            return (
              <CodeQuestionCard
                key={q.id}
                index={questions.indexOf(q) + 1}
                question={q}
                packageId={packageId}
                resourceId={resource.resource_id}
                userId={userId}
                sessionId={sessionId}
              />
            );
          }
          const submission = responses.submissions[q.id];
          const isSub = !!submission;
          const correct = submission?.correct === true;
          return (
            <QuestionCard
              key={q.id}
              index={questions.indexOf(q) + 1}
              question={q}
              isSub={isSub}
              correct={correct}
              submitting={responses.submitting[q.id] === true}
              answer={responses.drafts[q.id]}
              setAnswer={(v) => responses.setDraft(q.id, v)}
              submit={() => { void responses.submit(q.id); }}
              reset={() => responses.resetDraft(q.id)}
            />
          );
        })}
      </div>
    </div>
  );
}

function isCorrect(q: ParsedQuestion, ans: any): boolean {
  if (ans === undefined || ans === null) return false;
  if (q.type === "fill_blank") {
    // 2026-06-21 plan (B4): per-blank comparison with
    // trim + case-insensitive + numeric-tolerance
    const expected = Array.isArray(q.answer) ? q.answer : [q.answer];
    const given = Array.isArray(ans) ? ans : [ans];
    if (expected.length !== given.length) return false;
    return expected.every((exp: unknown, i: number) =>
      _normalizeFillBlank(String(exp), String(given[i] ?? ""))
    );
  }
  if (Array.isArray(q.answer)) {
    if (!Array.isArray(ans)) return false;
    const setA = new Set<string>(q.answer.map(String));
    const setB = new Set<string>(ans.map(String));
    return setA.size === setB.size && [...setA].every((x) => setB.has(x));
  }
  if (typeof q.answer === "boolean") {
    return ans === q.answer;
  }
  return String(ans).trim().toLowerCase() === String(q.answer).trim().toLowerCase();
}

/**
 * Compare a single fill-in-the-blank slot.
 *
 * Rules (2026-06-21 plan):
 *  - Trim leading/trailing whitespace from both sides.
 *  - Case-insensitive comparison.
 *  - Numeric tolerance: if both sides parse as finite float,
 *    accept when |a - b| / max(1, |b|) < 0.05.
 */
function _normalizeFillBlank(expected: string, given: string): boolean {
  const e = expected.trim().toLowerCase();
  const g = given.trim().toLowerCase();
  if (e === g) return true;
  const numE = parseFloat(e);
  const numG = parseFloat(g);
  if (!isNaN(numE) && !isNaN(numG)) {
    const tol = Math.abs(numE - numG) / Math.max(1, Math.abs(numE));
    return tol < 0.05;
  }
  return false;
}

/**
 * Split a question text like "The capital of ___ is ___."
 * into segments of {text, blankKey}. Blank slots with no
 * explicit label get an auto-label like "blank_0".
 */
function _splitBlanks(questionText: string): Array<{ text: string; blankKey?: string }> {
  const parts = questionText.split(/_{3,}/);
  const segments: Array<{ text: string; blankKey?: string }> = [];
  parts.forEach((p, i) => {
    if (p) segments.push({ text: p });
    if (i < parts.length - 1) {
      segments.push({ text: "", blankKey: `blank_${i}` });
    }
  });
  return segments.length > 0 ? segments : [{ text: questionText }];
}

function QuestionCard({
  index,
  question: q,
  isSub,
  correct,
  submitting,
  answer,
  setAnswer,
  submit,
  reset,
}: {
  index: number;
  question: ParsedQuestion;
  isSub: boolean;
  correct: boolean;
  submitting: boolean;
  answer: any;
  setAnswer: (v: any) => void;
  submit: () => void;
  reset: () => void;
}) {
  return (
    <div
      className={cn(
        "p-4 rounded-lg border bg-bg-card",
        isSub
          ? correct
            ? "border-green-700/50 bg-green-950/20"
            : "border-red-700/50 bg-red-950/20"
          : "border-fg/10",
      )}
    >
      <div className="flex items-center justify-between mb-2 text-xs text-fg-muted">
        <span className="flex items-center gap-2">
          <span className="font-mono text-fg-subtle">#{index}</span>
          <span>{q.knowledge_point || "通用"}</span>
          <span>·</span>
          <span>难度 {"★".repeat(q.difficulty)}</span>
          <span className="px-1.5 py-0.5 rounded bg-bg-panel border border-fg/10 text-[10px]">
            {TYPE_LABELS[q.type] || q.type}
          </span>
        </span>
        {isSub && (
          <span className={correct ? "text-green-400 flex items-center gap-1" : "text-red-400 flex items-center gap-1"}>
            {correct ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
            {correct ? "正确" : "错误"}
          </span>
        )}
      </div>
      <p className="text-sm text-fg mb-3 leading-relaxed">{q.question}</p>
      {q.type === "single_choice" && q.options.length > 0 && (
        <div className="space-y-2">
          {q.options.map((opt, index) => {
            const checked = answer === opt.label;
            const isAnswer = String(q.answer) === opt.label;
            return (
              <label
                key={JSON.stringify([q.id, opt.label || "option", index])}
                className={cn(
                  "flex items-start gap-2 p-2.5 rounded-lg border cursor-pointer transition-colors",
                  checked
                    ? "border-brand-500 bg-brand-950/30"
                    : "border-fg/10 hover:border-fg/20",
                  isSub && isAnswer && "border-green-500 bg-green-950/30",
                  isSub && checked && !isAnswer && "border-red-500 bg-red-950/30",
                  isSub && "cursor-default",
                )}
              >
                <input
                  type="radio"
                  name={q.id}
                  value={opt.label}
                  checked={checked}
                  disabled={isSub}
                  onChange={() => setAnswer(opt.label)}
                  className="mt-1 accent-brand-500"
                />
                <span className="text-sm flex-1">
                  <strong className="font-mono text-brand-300">
                    {opt.label}.
                  </strong>{" "}
                  {opt.text}
                </span>
              </label>
            );
          })}
        </div>
      )}
      {q.type === "multiple_choice" && q.options.length > 0 && (
        <div className="space-y-2">
          {q.options.map((opt, index) => {
            const current = Array.isArray(answer) ? answer : [];
            const checked = current.includes(opt.label);
            const isAnswer = Array.isArray(q.answer)
              ? (q.answer as string[]).includes(opt.label)
              : String(q.answer) === opt.label;
            return (
              <label
                key={JSON.stringify([q.id, opt.label || "option", index])}
                className={cn(
                  "flex items-start gap-2 p-2.5 rounded-lg border cursor-pointer transition-colors",
                  checked
                    ? "border-brand-500 bg-brand-950/30"
                    : "border-fg/10 hover:border-fg/20",
                  isSub && isAnswer && "border-green-500 bg-green-950/30",
                  isSub && checked && !isAnswer && "border-red-500 bg-red-950/30",
                  isSub && "cursor-default",
                )}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={isSub}
                  onChange={() => {
                    if (isSub) return;
                    const next = checked
                      ? current.filter((x: string) => x !== opt.label)
                      : [...current, opt.label];
                    setAnswer(next);
                  }}
                  className="mt-1 accent-brand-500"
                />
                <span className="text-sm flex-1">
                  <strong className="font-mono text-brand-300">
                    {opt.label}.
                  </strong>{" "}
                  {opt.text}
                </span>
              </label>
            );
          })}
        </div>
      )}
      {q.type === "true_false" && (
        <div className="flex gap-2">
          {[true, false].map((v) => {
            const checked = answer === v;
            const isAnswer = q.answer === v;
            return (
              <button
                key={String(v)}
                onClick={() => !isSub && setAnswer(v)}
                disabled={isSub}
                className={cn(
                  "px-4 py-1.5 rounded-md text-sm border transition-colors",
                  checked
                    ? "border-brand-500 bg-brand-950/30"
                    : "border-fg/10 hover:border-fg/20",
                  isSub && isAnswer && "border-green-500 bg-green-950/30",
                  isSub && checked && !isAnswer && "border-red-500 bg-red-950/30",
                )}
              >
                {v ? "✓ 正确" : "✗ 错误"}
              </button>
            );
          })}
        </div>
      )}
      {q.type === "short_answer" && (
        <input
          type="text"
          value={answer || ""}
          disabled={isSub}
          onChange={(e) => setAnswer(e.target.value)}
          className="w-full bg-bg-panel border border-fg/10 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-brand-500"
          placeholder="输入答案…"
        />
      )}
      {q.type === "fill_blank" && (
        <FillBlankInput
          question={q}
          answer={answer}
          isSub={isSub}
          setAnswer={setAnswer}
        />
      )}
      <div className="flex items-center gap-2 mt-3">
        {!isSub ? (
          <button
            onClick={submit}
            disabled={answer === undefined || submitting}
            className={cn(
              "btn-primary text-xs px-3 py-1.5",
              (answer === undefined || submitting) && "opacity-50 cursor-not-allowed",
            )}
          >
            <Check className="w-3 h-3" />
            {submitting ? "提交中…" : "提交"}
          </button>
        ) : (
          <button onClick={reset} className="btn-ghost text-xs px-3 py-1.5">
            <RotateCcw className="w-3 h-3" />
            重做
          </button>
        )}
        {isSub && q.explanation && (
          <div className="text-xs text-fg-muted ml-2 flex-1 leading-relaxed">
            <span className="font-semibold text-brand-300">解析：</span>
            {q.explanation}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fill-in-the-blank input (2026-06-21 plan, B4)
// Supports single-blank and multi-blank with per-slot correctness.
// ---------------------------------------------------------------------------

function FillBlankInput({
  question: q,
  answer,
  isSub,
  setAnswer,
}: {
  question: ParsedQuestion;
  answer: string[];
  isSub: boolean;
  setAnswer: (v: string[]) => void;
}) {
  const segments = useMemo(() => _splitBlanks(q.question), [q.question]);
  const expected = Array.isArray(q.answer) ? q.answer : [q.answer];

  if (segments.length === 1 && !segments[0].blankKey) {
    // No blank markers found — render a single input.
    return (
      <input
        type="text"
        value={Array.isArray(answer) ? (answer[0] ?? "") : (answer ?? "")}
        disabled={isSub}
        onChange={(e) => setAnswer([e.target.value])}
        className="w-full bg-bg-panel border border-fg/10 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-brand-500"
        placeholder="输入答案…"
      />
    );
  }

  return (
    <div className="text-sm text-fg leading-relaxed space-y-1">
      {segments.map((seg, i) => {
        if (seg.blankKey === undefined) {
          return seg.text ? <span key={i}>{seg.text} </span> : null;
        }
        const blankIdx = parseInt(seg.blankKey.replace("blank_", ""), 10);
        const userVal = (Array.isArray(answer) ? answer[blankIdx] : "") ?? "";
        const exp = String(expected[blankIdx] ?? "");
        const slotCorrect = isSub
          ? _normalizeFillBlank(exp, String(userVal))
          : null;
        return (
          <span key={seg.blankKey} className="inline-flex items-center gap-1 mx-0.5 align-baseline">
            {!isSub ? (
              <input
                type="text"
                value={userVal}
                onChange={(e) => {
                  const next = Array.isArray(answer) ? [...answer] : [];
                  next[blankIdx] = e.target.value;
                  setAnswer(next);
                }}
                className="bg-bg-panel border border-fg/10 rounded px-2 py-0.5 text-sm w-28 font-mono focus:outline-none focus:border-brand-500"
                placeholder="…"
              />
            ) : (
              <span
                className={cn(
                  "px-2 py-0.5 rounded text-sm font-mono border",
                  slotCorrect
                    ? "bg-green-950/20 border-green-700/50 text-green-300"
                    : "bg-red-950/20 border-red-700/50 text-red-300",
                )}
              >
                {userVal || "(空)"}
                {slotCorrect ? (
                  <Check className="w-3 h-3 inline ml-1" />
                ) : (
                  <X className="w-3 h-3 inline ml-1" />
                )}
              </span>
            )}
            {isSub && slotCorrect === false && exp && (
              <span className="text-[10px] text-green-400">({exp})</span>
            )}
            {segments[i + 1]?.text ? (
              <span>{segments[i + 1].text}</span>
            ) : null}
          </span>
        );
      })}
    </div>
  );
}

function resolveDurablePackageId(
  resource: Resource,
  latestPackage: { package_id: string; resources: Resource[] } | null,
) {
  if (resource.metadata?.package_persisted === false) return null;
  const metadataId =
    typeof resource.metadata?.package_id === "string"
      ? resource.metadata.package_id
      : "";
  if (
    resource.metadata?.package_persisted === true &&
    metadataId &&
    !isPendingPackage(metadataId)
  ) {
    return metadataId;
  }
  if (
    latestPackage &&
    !isPendingPackage(latestPackage.package_id) &&
    latestPackage.resources.some(
      (item) =>
        item.resource_id === resource.resource_id &&
        item.metadata?.package_persisted === true,
    )
  ) {
    return latestPackage.package_id;
  }
  return null;
}

function isPendingPackage(packageId: string) {
  return packageId.startsWith("pending-") || packageId.startsWith("partial-");
}

function CodeQuestionCard({
  index,
  question,
  packageId,
  resourceId,
  userId,
  sessionId,
}: {
  index: number;
  question: ParsedQuestion;
  packageId: string | null;
  resourceId: string;
  userId: string;
  sessionId: string;
}) {
  const codeQuestion: CodeExerciseQuestion = {
    id: question.id,
    type: "code",
    difficulty: question.difficulty,
    knowledge_point: question.knowledge_point,
    question: question.question,
    options: question.options,
    explanation: question.explanation,
    code_spec: question.code_spec,
  };
  return (
    <div className="rounded-lg border border-fg/10 bg-bg-card p-4">
      <div className="mb-2 flex items-center gap-2 text-xs text-fg-muted">
        <span className="font-mono text-fg-subtle">#{index}</span>
        <span>难度 {"★".repeat(question.difficulty)}</span>
        <span className="rounded border border-fg/10 bg-bg-panel px-1.5 py-0.5 text-[10px]">代码</span>
      </div>
      <p className="text-sm leading-relaxed text-fg">{question.question}</p>
      <CodeExerciseEditor
        question={codeQuestion}
        packageId={packageId}
        resourceId={resourceId}
        userId={userId}
        sessionId={sessionId}
      />
    </div>
  );
}
