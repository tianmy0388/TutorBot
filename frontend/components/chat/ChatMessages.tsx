"use client";

import { useEffect, useMemo, useRef, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { AlertTriangle, Check, LoaderCircle } from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { isTerminal } from "@/lib/job-reducer";
import { cn } from "@/lib/utils";

const STAGE_LABELS: Record<string, string> = {
  intent: "理解目标",
  understand: "理解目标",
  profile: "读取学习状态",
  knowledge: "查找课程资料",
  rag: "查找课程资料",
  retrieval: "查找课程资料",
  plan: "整理下一步",
  path: "整理下一步",
  content: "整理讲解",
  pedagogy: "整理讲解",
  answer: "整理讲解",
  exercise: "准备练习",
  review: "检查内容",
  safety: "检查内容",
  assessment: "整理练习结果",
  render: "准备可视资料",
};

export function ChatMessages({ emptyState }: { emptyState?: ReactNode } = {}) {
  const messages = useTutorStore((state) => state.messages);
  const activeTurn = useTutorStore((state) => state.activeTurn);
  const jobsById = useTutorStore((state) => state.jobsById);
  const jobOrder = useTutorStore((state) => state.jobOrder);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const liveJob = useMemo(() => {
    for (let index = jobOrder.length - 1; index >= 0; index -= 1) {
      const job = jobsById[jobOrder[index]];
      if (job && !isTerminal(job.status)) return job;
    }
    return null;
  }, [jobOrder, jobsById]);

  const textBuffer = liveJob?.text_buffer || activeTurn.text_buffer;
  const errorText = liveJob?.error || activeTurn.error;
  const stage = liveJob?.stage || "";

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    if (messages.length === 0 && !liveJob) {
      element.scrollTop = 0;
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [messages.length, liveJob, liveJob?.text_buffer]);

  const empty = messages.length === 0 && !liveJob;
  return (
    <div ref={scrollRef} className={cn("flex-1 overflow-y-auto", empty ? "p-0" : "px-5 py-8 sm:px-8 lg:px-10")}>
      {empty ? emptyState || <div className="flex min-h-full items-center justify-center text-sm text-fg-muted">选择或建立一个学习任务。</div> : null}
      {!empty && (
        <div className="mx-auto max-w-[880px]">
          {messages.map((message) => <MessageDocument key={message.id} message={message} />)}
          {liveJob && <ActiveTask text={textBuffer} stage={stage} error={errorText} />}
        </div>
      )}
    </div>
  );
}

function MessageDocument({ message }: { message: { id: string; role: string; content: string; timestamp: number } }) {
  const user = message.role === "user";
  const error = message.role === "system" && /错误|失败/.test(message.content);
  return (
    <section className="border-b border-border py-7 first:pt-0 animate-fade-in">
      <div className="flex items-center justify-between gap-4 text-[11px] font-semibold text-fg-muted">
        <span>{user ? "你的学习目标" : error ? "需要处理" : "TutorBot 的整理"}</span>
        <time className="font-normal text-fg-subtle">{formatTime(message.timestamp)}</time>
      </div>
      <div className={cn("mt-3", user && "rounded-3xl bg-bg-subtle px-5 py-4 text-base font-medium", error && "rounded-2xl border border-border px-4 py-3")}>
        {error && <AlertTriangle className="mr-2 inline h-4 w-4" />}
        <MarkdownContent content={message.content} />
      </div>
    </section>
  );
}

function ActiveTask({ text, stage, error }: { text: string; stage: string; error: string | null }) {
  const label = naturalStage(stage);
  return (
    <section className="py-7 animate-fade-in" aria-live="polite">
      <div className="flex items-center gap-2 text-xs font-semibold text-fg-muted">
        {text ? <Check className="h-4 w-4" /> : <LoaderCircle className="h-4 w-4 animate-spin" />}
        {text ? "正在整理回答" : label}
      </div>
      {text && <div className="mt-4"><MarkdownContent content={text} streaming /></div>}
      {!text && !error && <p className="mt-3 text-sm text-fg-muted">请稍等一下，完成的内容会直接出现在这里。</p>}
      {error && (
        <div className="mt-4 rounded-2xl border border-border bg-bg-subtle p-4 text-sm">
          <p className="font-semibold">这一步暂时没有完成</p>
          <p className="mt-1 text-fg-muted">可以稍后重试，已经完成的内容不会丢失。</p>
          <details className="mt-3 text-xs text-fg-muted"><summary className="cursor-pointer font-medium text-fg">来源与说明</summary><p className="mt-2 leading-5">{error}</p></details>
        </div>
      )}
    </section>
  );
}

function naturalStage(stage: string) {
  const normalized = stage.toLowerCase();
  for (const [key, label] of Object.entries(STAGE_LABELS)) {
    if (normalized.includes(key)) return label;
  }
  return "准备学习内容";
}

function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function MarkdownContent({ content, streaming = false }: { content: string; streaming?: boolean }) {
  if (!content.trim()) return null;
  return (
    <div className={cn("prose-tutor text-sm leading-7", streaming && "streaming-cursor")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
          code: ({ node, className, children, ...props }) => className
            ? <code className={className} {...props}>{children}</code>
            : <code className="rounded bg-bg-subtle px-1.5 py-0.5 text-xs" {...props}>{children}</code>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
