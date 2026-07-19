"use client";

import { useEffect, useMemo, useRef, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { AlertTriangle, Check, LoaderCircle, X } from "lucide-react";

import { isJobTerminal } from "@/lib/job-reducer";
import { useTutorStore } from "@/lib/store";
import type {
  ChatMessage,
  StructuredError,
  WorkflowSnapshot,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const STAGE_LABELS: Record<string, string> = {
  intent: "理解目标",
  understand: "理解目标",
  question: "理解问题",
  profile: "读取学习状态",
  knowledge: "查找课程资料",
  context: "查找课程资料",
  rag: "查找课程资料",
  retrieval: "查找课程资料",
  resource_planning: "整理下一步",
  path: "整理下一步",
  content: "整理讲解",
  pedagogy: "整理讲解",
  answer: "整理讲解",
  exercise: "准备练习",
  reading: "准备学习资料",
  mindmap: "准备学习资料",
  video: "准备可视资料",
  code: "准备示例",
  parallel_resource: "准备学习资料",
  review: "检查内容",
  safety: "检查内容",
  hallucination: "检查内容",
  fact_check: "检查内容",
  assessment: "整理练习结果",
  adaptive: "安排下一步",
  event: "更新学习状态",
  persist: "保存学习记录",
  session_recording: "保存学习记录",
  package: "整理学习资料",
};

export function ChatMessages({ emptyState }: { emptyState?: ReactNode } = {}) {
  const messages = useTutorStore((state) => state.messages);
  const jobsById = useTutorStore((state) => state.jobsById);
  const jobOrder = useTutorStore((state) => state.jobOrder);
  const recoveryWarnings = useTutorStore((state) => state.recoveryWarnings);
  const dismissRecoveryWarning = useTutorStore(
    (state) => state.dismissRecoveryWarning,
  );
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const liveJob = useMemo(() => {
    for (const jobId of jobOrder) {
      const job = jobsById[jobId];
      if (job && !isJobTerminal(job)) return job;
    }
    return null;
  }, [jobOrder, jobsById]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    if (messages.length === 0 && !liveJob) {
      element.scrollTop = 0;
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [messages.length, liveJob?.events.length, liveJob?.text_buffer]);

  const empty = messages.length === 0 && !liveJob;

  return (
    <div
      ref={scrollRef}
      className={cn(
        "flex-1 overflow-y-auto",
        empty && recoveryWarnings.length === 0
          ? "p-0"
          : "px-5 py-8 sm:px-8 lg:px-10",
      )}
    >
      {empty && recoveryWarnings.length === 0
        ? emptyState || (
            <div className="flex min-h-full items-center justify-center text-sm text-fg-muted">
              选择或建立一个学习任务。
            </div>
          )
        : null}

      {(recoveryWarnings.length > 0 || !empty) && (
        <div className="mx-auto max-w-[880px]">
          {recoveryWarnings.map((warning, index) => (
            <div
              key={`${warning.code}-${warning.resource_id ?? warning.job_id ?? index}`}
              className="mb-4 flex items-start gap-3 rounded-2xl border border-border bg-bg-subtle px-4 py-3 text-sm text-fg"
              role="status"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted" />
              <span className="min-w-0 flex-1 leading-6">{warning.message}</span>
              <button
                type="button"
                aria-label="关闭恢复提示"
                className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full text-fg-muted hover:bg-bg-panel hover:text-fg"
                onClick={() => dismissRecoveryWarning(index)}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}

          {empty
            ? emptyState || (
                <div className="flex min-h-48 items-center justify-center text-sm text-fg-muted">
                  选择或建立一个学习任务。
                </div>
              )
            : null}
          {messages.map((message) => (
            <MessageDocument key={message.id} message={message} />
          ))}
          {liveJob && (
            <ActiveTask
              text={liveJob.text_buffer}
              stage={liveJob.stage}
              error={liveJob.error}
            />
          )}
        </div>
      )}
    </div>
  );
}

function MessageDocument({ message }: { message: ChatMessage }) {
  const workflow = workflowFromMetadata(message.metadata);
  if (workflow) return <WorkflowDocument workflow={workflow} />;

  const user = message.role === "user";
  const protocolError = message.metadata?.protocol_error === true;
  const error = message.role === "system" && (/错误|失败/.test(message.content) || protocolError);
  const visibleContent = protocolError
    ? "这一步暂时没有完成，请稍后重试。"
    : message.content;

  return (
    <section className="animate-fade-in border-b border-border py-7 first:pt-0">
      <div className="flex items-center justify-between gap-4 text-[11px] font-semibold text-fg-muted">
        <span>{user ? "你的学习目标" : error ? "需要处理" : "TutorBot 的整理"}</span>
        <time className="font-normal text-fg-subtle">
          {formatTime(message.timestamp)}
        </time>
      </div>
      <div
        className={cn(
          "mt-3",
          user && "rounded-3xl bg-bg-subtle px-5 py-4 text-base font-medium",
          error && "rounded-2xl border border-border px-4 py-3",
        )}
      >
        {error && <AlertTriangle className="mr-2 inline h-4 w-4" />}
        <MarkdownContent content={visibleContent} />
        {protocolError && (
          <details className="mt-3 text-xs text-fg-muted">
            <summary className="cursor-pointer font-medium text-fg">
              来源与说明
            </summary>
            <p className="mt-2 leading-5">{message.content}</p>
          </details>
        )}
      </div>
    </section>
  );
}

function ActiveTask({
  text,
  stage,
  error,
}: {
  text: string;
  stage: string;
  error: StructuredError | null;
}) {
  return (
    <section className="animate-fade-in py-7" aria-live="polite">
      <div className="flex items-center gap-2 text-xs font-semibold text-fg-muted">
        {error ? (
          <AlertTriangle className="h-4 w-4" />
        ) : (
          <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" />
        )}
        {error ? "需要再试一次" : text ? "正在整理回答" : naturalStage(stage)}
      </div>
      {text && (
        <div className="mt-4">
          <MarkdownContent content={text} streaming />
        </div>
      )}
      {!text && !error && (
        <p className="mt-3 text-sm text-fg-muted">
          请稍等一下，完成的内容会直接出现在这里。
        </p>
      )}
      {error && (
        <div className="mt-4 rounded-2xl border border-border bg-bg-subtle p-4 text-sm">
          <p className="font-semibold">这一步暂时没有完成</p>
          <p className="mt-1 text-fg-muted">{error.message}</p>
          <details className="mt-3 text-xs text-fg-muted">
            <summary className="cursor-pointer font-medium text-fg">
              来源与说明
            </summary>
            <p className="mt-2 leading-5">错误编号：{error.code}</p>
          </details>
        </div>
      )}
    </section>
  );
}

function WorkflowDocument({ workflow }: { workflow: WorkflowSnapshot }) {
  const statusLabel: Record<WorkflowSnapshot["status"], string> = {
    succeeded: "已完成",
    partial: "已完成主要内容",
    failed: "需要再试一次",
    cancelled: "已停止",
  };
  const stages = Array.from(
    new Map(
      workflow.stages.map((stage) => [naturalStage(stage.name), stage.status]),
    ).entries(),
  );

  return (
    <section className="animate-fade-in border-b border-border py-7 first:pt-0">
      <div className="flex items-center justify-between gap-4 text-[11px] font-semibold text-fg-muted">
        <span>这次学习</span>
        <span>{statusLabel[workflow.status]}</span>
      </div>
      {stages.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {stages.map(([label, status]) => (
            <span
              key={label}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border bg-bg-subtle px-3 text-xs text-fg-muted"
            >
              {status === "completed" ? (
                <Check className="h-3.5 w-3.5" />
              ) : (
                <span className="h-1.5 w-1.5 rounded-full bg-fg-muted" />
              )}
              {label}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function workflowFromMetadata(
  metadata: Record<string, unknown> | undefined,
): WorkflowSnapshot | null {
  if (metadata?.kind !== "workflow_timeline") return null;
  const workflow = metadata.workflow;
  if (!workflow || typeof workflow !== "object") return null;
  const candidate = workflow as Partial<WorkflowSnapshot>;
  if (
    !Array.isArray(candidate.stages) ||
    !["succeeded", "partial", "failed", "cancelled"].includes(
      candidate.status ?? "",
    )
  ) {
    return null;
  }
  if (
    !candidate.stages.every(
      (stage) =>
        !!stage &&
        typeof stage === "object" &&
        typeof (stage as { name?: unknown }).name === "string" &&
        ["completed", "incomplete"].includes(
          (stage as { status?: unknown }).status as string,
        ),
    )
  ) {
    return null;
  }
  return candidate as WorkflowSnapshot;
}

function naturalStage(stage: string) {
  const normalized = stage.toLowerCase();
  for (const [key, label] of Object.entries(STAGE_LABELS)) {
    if (normalized.includes(key)) return label;
  }
  return "准备学习内容";
}

function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function MarkdownContent({
  content,
  streaming = false,
}: {
  content: string;
  streaming?: boolean;
}) {
  if (!content.trim()) return null;
  return (
    <div
      className={cn(
        "prose-tutor text-sm leading-7",
        streaming && "streaming-cursor",
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
          code: ({ node, className, children, ...props }) =>
            className ? (
              <code className={className} {...props}>
                {children}
              </code>
            ) : (
              <code
                className="rounded bg-bg-subtle px-1.5 py-0.5 text-xs"
                {...props}
              >
                {children}
              </code>
            ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
