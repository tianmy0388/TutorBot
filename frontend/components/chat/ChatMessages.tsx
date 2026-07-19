"use client";

import { useEffect, useMemo, useRef, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { AlertTriangle, X } from "lucide-react";

import { isJobTerminal } from "@/lib/job-reducer";
import { useTutorStore } from "@/lib/store";
import {
  taskProcessFromJob,
  taskProcessFromWorkflowMessage,
} from "@/lib/task-process";
import { TaskProcessCard } from "./TaskProcessCard";
import type { ChatMessage } from "@/lib/types";
import { cn } from "@/lib/utils";

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
          {liveJob && <TaskProcessCard data={taskProcessFromJob(liveJob)} />}
        </div>
      )}
    </div>
  );
}

function MessageDocument({ message }: { message: ChatMessage }) {
  const taskProcess = taskProcessFromWorkflowMessage(message);
  if (taskProcess) return <TaskProcessCard data={taskProcess} />;

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

function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function MarkdownContent({ content }: { content: string }) {
  if (!content.trim()) return null;
  return (
    <div className={cn("prose-tutor text-sm leading-7")}>
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
