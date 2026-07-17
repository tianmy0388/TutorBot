"use client";

/**
 * ChatMessages — render chat history + live streaming output.
 *
 * State source: this component reads from ``jobsById`` (the per-job
 * state managed by ``lib/job-reducer``). The legacy ``activeTurn``
 * record is no longer the source of truth for the live-turn view;
 * it is kept on the store for compatibility (older code paths still
 * read it) but the chat surface derives everything — text,
 * thinking, stage, error, and "is a turn in progress?" — from the
 * per-job events. The "in progress" check is now
 * ``!isJobTerminal(job)`` rather than
 * ``activeTurn.phase !== "idle"``, which used to leave the spinner
 * hanging after ``job_terminal`` (the regression called out in the
 * 2026-06-21 stability plan).
 */

import { useEffect, useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import { StageIndicator, StageRow } from "./StageIndicator";
import { isJobTerminal } from "@/lib/job-reducer";
import {
  Bot,
  User,
  Sparkles,
  AlertTriangle,
  Wrench,
  ListTree,
  Cpu,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Top-level
// ---------------------------------------------------------------------------

export function ChatMessages() {
  const messages = useTutorStore((s) => s.messages);
  const jobsById = useTutorStore((s) => s.jobsById);
  const jobOrder = useTutorStore((s) => s.jobOrder);
  const tracePanelOpen = useTutorStore((s) => s.tracePanelOpen);
  const recoveryWarnings = useTutorStore((s) => s.recoveryWarnings);
  const dismissRecoveryWarning = useTutorStore((s) => s.dismissRecoveryWarning);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // "In progress" is defined by the per-job state machine, NOT the
  // legacy activeTurn.phase. The most-recent non-terminal job is the
  // source of truth for "show the spinner / streaming view". Once
  // every job is terminal, the view collapses to the message list —
  // the "正在调用 Agent" badge no longer hangs because we no longer
  // use phase !== idle as the trigger. Streaming buffers are owned by
  // that same durable ClientJob.
  const liveJob = useMemo(() => {
    // jobOrder is newest-first, so the first live entry is the latest job.
    for (const jobId of jobOrder) {
      const job = jobsById[jobId];
      if (job && !isJobTerminal(job)) return job;
    }
    return null;
  }, [jobOrder, jobsById]);

  // The live view follows the job. If a job is running, we render
  // it. If every job is terminal — even if the legacy activeTurn
  // record still claims to be "streaming" or "success" — the live
  // view stays collapsed and the message list takes over.
  const showLive = !!liveJob;
  // Streaming output is read only from the durable per-job state.
  const textBuffer = liveJob?.text_buffer ?? "";
  const thinkingBuffer = liveJob?.thinking_buffer ?? "";
  const errorText = liveJob?.error ?? null;
  const events = liveJob?.events ?? [];
  const stage = liveJob?.stage || "";

  // Auto-scroll on new content
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [
    messages.length,
    liveJob?.text_buffer,
    liveJob?.thinking_buffer,
    liveJob?.events.length,
  ]);

  return (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto px-6 py-6 space-y-5"
    >
      {recoveryWarnings.map((warning, index) => (
        <div
          key={`${warning.code}-${warning.resource_id ?? warning.job_id ?? index}`}
          className="max-w-3xl mx-auto flex items-start gap-2 rounded-lg border border-amber-700/40 bg-amber-950/25 px-3 py-2 text-sm text-amber-100"
          role="status"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
          <span className="flex-1">{warning.message}</span>
          <button
            type="button"
            aria-label="关闭恢复提示"
            className="rounded px-1 text-amber-200 hover:bg-amber-900/40"
            onClick={() => dismissRecoveryWarning(index)}
          >
            ×
          </button>
        </div>
      ))}
      {messages.length === 0 && !showLive ? <EmptyState /> : null}
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}

      {showLive && (
        <ActiveTurnView
          textBuffer={textBuffer}
          thinkingBuffer={thinkingBuffer}
          stage={stage}
          showTrace={tracePanelOpen}
          events={events}
          error={errorText}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  const setCapability = useTutorStore((s) => s.setCurrentCapability);
  return (
    <div className="max-w-3xl mx-auto py-12">
      <div className="card text-center animate-fade-in">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-gradient-to-br from-brand-500 to-accent mb-4">
          <Sparkles className="w-6 h-6 text-white" />
        </div>
        <h2 className="text-2xl font-bold mb-2">欢迎使用 Tutor</h2>
        <p className="text-fg-muted mb-6">
          多智能体学习系统 · 选择一个能力开始
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-left">
          <SuggestionButton
            label="生成学习资源"
            desc="系统学习画像 + 知识图谱 → 6+ 类资源"
            onClick={() => setCapability("resource_generation")}
            example="例如：系统学习 LSTM"
            icon={Sparkles}
            accent="text-accent"
          />
          <SuggestionButton
            label="即时答疑"
            desc="理解问题 → 检索知识库 → 4 层解答"
            onClick={() => setCapability("tutoring")}
            example="例如：什么是注意力机制？"
            icon={Bot}
            accent="text-brand-300"
          />
          <SuggestionButton
            label="学习效果评估"
            desc="多维度评估 → 自适应推送策略"
            onClick={() => setCapability("assessment")}
            example="评估一下我"
            icon={ListTree}
            accent="text-green-400"
          />
          <SuggestionButton
            label="路径规划"
            desc="基于知识图谱推荐下一步学习节点"
            onClick={() => setCapability("path_planning")}
            example="下一步该学什么？"
            icon={Cpu}
            accent="text-yellow-300"
          />
        </div>
      </div>
    </div>
  );
}

function SuggestionButton(props: {
  label: string;
  desc: string;
  example: string;
  onClick: () => void;
  icon: any;
  accent: string;
}) {
  const Icon = props.icon;
  return (
    <button
      onClick={props.onClick}
      className="p-4 rounded-lg border border-fg/10 bg-bg-panel hover:border-brand-500/40 hover:bg-bg-card transition-all text-left group"
    >
      <div className="flex items-start gap-2 mb-1">
        <Icon className={cn("w-4 h-4 mt-0.5 shrink-0", props.accent)} />
        <span className="font-medium">{props.label}</span>
      </div>
      <div className="text-xs text-fg-muted">{props.desc}</div>
      <div className="text-xs text-brand-400 mt-2 italic group-hover:text-brand-300">
        "{props.example}"
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Historical message bubble
// ---------------------------------------------------------------------------

function MessageBubble({
  message,
}: {
  message: {
    id: string;
    role: string;
    content: string;
    agent?: string;
    timestamp: number;
  };
}) {
  const isUser = message.role === "user";
  const isError = message.role === "system" && message.content.startsWith("错误");
  const Icon = isUser ? User : isError ? AlertTriangle : Bot;
  return (
    <div
      className={cn(
        "flex gap-3 max-w-3xl animate-fade-in",
        isUser ? "ml-auto flex-row-reverse" : "mr-auto",
      )}
    >
      <div
        className={cn(
          "shrink-0 w-8 h-8 rounded-lg flex items-center justify-center shadow-sm",
          isUser
            ? "bg-gradient-to-br from-brand-500 to-brand-700 text-white"
            : isError
            ? "bg-red-900/40 text-red-300"
            : "bg-bg-card text-fg-muted border border-fg/10",
        )}
      >
        <Icon className="w-4 h-4" />
      </div>
      <div
        className={cn(
          "card max-w-[80%]",
          isUser && "bg-brand-900/30 border-brand-700/40",
          isError && "bg-red-950/30 border-red-800/40",
        )}
      >
        {!isUser && message.agent && (
          <div className="text-[10px] text-fg-muted mb-1 flex items-center gap-1">
            <Bot className="w-3 h-3" />
            <span>{message.agent}</span>
            <span className="ml-auto text-fg-subtle">
              {formatTime(message.timestamp)}
            </span>
          </div>
        )}
        {isUser && (
          <div className="text-[10px] text-brand-300/70 mb-1 text-right">
            {formatTime(message.timestamp)}
          </div>
        )}
        <MarkdownContent content={message.content} />
      </div>
    </div>
  );
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Active turn view (live streaming)
// ---------------------------------------------------------------------------

function ActiveTurnView(props: {
  textBuffer: string;
  thinkingBuffer: string;
  stage: string;
  showTrace: boolean;
  events: Array<{ type: string; stage: string; source: string; content: string }>;
  error: string | null;
}) {
  // Thinking is shown by default while the model is working. Once the final
  // text starts streaming in (``textBuffer`` non-empty) we auto-collapse it
  // so the answer takes the spotlight.
  const thinkingOpen = !props.textBuffer && !props.error;

  // Per-stage thinking items (most recent on top, capped at 6).
  const stageThinking = props.events
    .filter((e) => e.type === "thinking")
    .slice(-6)
    .reverse();

  return (
    <div className="max-w-3xl mr-auto animate-fade-in">
      <div className="card bg-bg-panel/80 border-fg/10">
        {/* Stage header */}
        <StageIndicator currentStage={props.stage} />

        {/* Pipeline stage progress */}
        {props.showTrace && props.events.length > 0 && (
          <StageProgress events={props.events} />
        )}

        {/* Thinking — always visible during wait, auto-collapses on final output */}
        {(props.thinkingBuffer || stageThinking.length > 0) && (
          <details
            className="mt-3 group"
            open={thinkingOpen}
          >
            <summary
              className={cn(
                "text-xs cursor-pointer hover:text-fg transition-colors flex items-center gap-2",
                thinkingOpen ? "text-fg-muted" : "text-fg-subtle",
              )}
            >
              <span className="animate-pulse">💭</span>
              <span>
                {thinkingOpen
                  ? "思考中…（点击折叠）"
                  : `查看思考过程 (${props.thinkingBuffer.length} 字符)`}
              </span>
            </summary>

            {/* Per-stage thinking entries (latest first) */}
            {stageThinking.length > 0 && (
              <div className="mt-2 space-y-1.5">
                {stageThinking.map((e, i) => (
                  <div
                    key={`think_${i}`}
                    className="text-xs text-fg-muted bg-bg/60 rounded-lg p-2.5 border border-fg/5"
                  >
                    <div className="flex items-center gap-1.5 mb-1 text-[10px]">
                      <Bot className="w-3 h-3 text-accent" />
                      <span className="text-accent font-medium">
                        {e.source}
                      </span>
                      <span className="text-fg-subtle">·</span>
                      <span className="text-fg-subtle font-mono">
                        {e.stage}
                      </span>
                    </div>
                    <div className="whitespace-pre-wrap leading-relaxed">
                      {e.content}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Fallback / cumulative buffer when there are no per-stage entries */}
            {stageThinking.length === 0 && props.thinkingBuffer && (
              <pre className="mt-2 text-xs text-fg-muted whitespace-pre-wrap max-h-48 overflow-y-auto bg-bg/60 rounded-lg p-3">
                {props.thinkingBuffer}
              </pre>
            )}
          </details>
        )}

        {/* Live text */}
        {props.textBuffer && (
          <div className="mt-3">
            <MarkdownContent content={props.textBuffer} streaming />
          </div>
        )}

        {/* Loading placeholder — only when there's literally nothing else to show */}
        {!props.textBuffer &&
          !props.thinkingBuffer &&
          stageThinking.length === 0 &&
          !props.error && (
            <div className="mt-3 text-sm text-fg-muted flex items-center gap-2">
              <TypingIndicator />
              <span>正在调用 Agent…</span>
            </div>
          )}

        {/* Error */}
        {props.error && (
          <div className="mt-3 text-sm text-red-300 flex items-start gap-2 p-3 bg-red-950/30 border border-red-800/40 rounded-lg">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{props.error}</span>
          </div>
        )}

        {/* Trace timeline + tool calls */}
        {props.showTrace && props.events.length > 0 && (
          <TraceTimeline events={props.events} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Typing indicator (3-dot pulse)
// ---------------------------------------------------------------------------

function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-pulse" />
      <span
        className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-pulse"
        style={{ animationDelay: "150ms" }}
      />
      <span
        className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-pulse"
        style={{ animationDelay: "300ms" }}
      />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Stage progress (visualises which stage_start events have fired)
// ---------------------------------------------------------------------------

function StageProgress({
  events,
}: {
  events: Array<{ type: string; stage: string; source: string; content: string }>;
}) {
  // **2026-07-08 fix (585f367d):** compute the open-stages stack
  // from the event timeline so the "active" badge tracks whatever
  // is currently running, not just the last ``stage_start`` we
  // ever saw. Pre-fix, a trailing unmatched ``stage_start`` (e.g.
  // ``video_rendering`` whose end never fired after a 600s
  // timeout) looked active forever, even after job_terminal.
  const seen = new Set<string>();
  const sequence: string[] = [];
  const openStack: string[] = [];
  for (const e of events) {
    if (!e.stage) continue;
    if (e.type === "stage_start") {
      if (!seen.has(e.stage)) {
        sequence.push(e.stage);
        seen.add(e.stage);
      }
      openStack.push(e.stage);
    } else if (e.type === "stage_end") {
      const idx = openStack.lastIndexOf(e.stage);
      if (idx >= 0) openStack.splice(idx, 1);
    }
  }

  if (sequence.length === 0) return null;
  const activeSet = new Set(openStack);

  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[10px]">
      {sequence.map((stage, i) => {
        const isActive = activeSet.has(stage);
        const isDone = !isActive;
        return (
          <div key={`${stage}_${i}`} className="flex items-center gap-1">
            <StageRow stage={stage} state={isActive ? "active" : "done"} />
            {i < sequence.length - 1 && (
              <span className="text-fg-subtle">→</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trace timeline + tool calls
// ---------------------------------------------------------------------------

function TraceTimeline({
  events,
}: {
  events: Array<{ type: string; stage: string; source: string; content: string }>;
}) {
  // Filter & compress: tool_call + tool_result → chip
  const chips: Array<{ key: string; kind: string; text: string }> = [];
  let lastToolCall: { source: string; content: string } | null = null;

  events.forEach((e, i) => {
    if (e.type === "tool_call") {
      lastToolCall = { source: e.source, content: e.content };
    } else if (e.type === "tool_result" && lastToolCall) {
      chips.push({
        key: `tc_${i}`,
        kind: "tool",
        text: `${lastToolCall.source}: ${lastToolCall.content.slice(0, 60)}`,
      });
      lastToolCall = null;
    } else if (e.type === "observation" || e.type === "thinking") {
      chips.push({
        key: `obs_${i}`,
        kind: e.type,
        text: e.content.slice(0, 80),
      });
    }
  });

  const stageEvents = events.filter(
    (e) => e.type === "stage_start" || e.type === "stage_end",
  );

  return (
    <details className="mt-3 group" open>
      <summary className="text-xs text-fg-muted cursor-pointer hover:text-fg transition-colors flex items-center gap-2">
        <ListTree className="w-3 h-3" />
        追踪面板 ({events.length} 个事件)
        <span className="text-fg-subtle ml-1 group-open:hidden">▾</span>
      </summary>

      <div className="mt-2 space-y-1.5">
        {/* Stage lifecycle */}
        {stageEvents.length > 0 && (
          <div className="text-[10px] font-mono space-y-0.5 bg-bg/40 rounded p-2">
            {stageEvents.slice(-12).map((e, i) => (
              <div key={i} className="flex gap-2">
                <span
                  className={cn(
                    "shrink-0",
                    e.type === "stage_start"
                      ? "text-brand-400"
                      : "text-green-400",
                  )}
                >
                  {e.type === "stage_start" ? "▶" : "✓"}
                </span>
                <span className="text-fg">{e.stage}</span>
              </div>
            ))}
          </div>
        )}

        {/* Tool call chips */}
        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {chips.slice(-8).map((c) => (
              <div
                key={c.key}
                className="text-[10px] px-2 py-0.5 rounded-md bg-bg-panel border border-fg/10 text-fg-muted flex items-center gap-1"
                title={c.text}
              >
                <Wrench className="w-2.5 h-2.5 text-accent shrink-0" />
                <span className="truncate max-w-[180px]">{c.text}</span>
              </div>
            ))}
          </div>
        )}

        {/* Recent raw events */}
        <div className="text-[10px] font-mono space-y-0.5 max-h-32 overflow-y-auto bg-bg/40 rounded p-2">
          {events.slice(-12).map((e, i) => (
            <div key={`raw_${i}`} className="flex gap-2">
              <span className="text-fg-subtle shrink-0">{e.type}</span>
              <span className="text-brand-400 shrink-0">{e.source}</span>
              <span className="text-fg-muted shrink-0">{e.stage}</span>
              <span className="truncate text-fg-subtle">{e.content}</span>
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Markdown renderer
// ---------------------------------------------------------------------------

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
        "prose-tutor text-sm leading-relaxed",
        streaming && "after:content-['▍'] after:text-brand-400 after:animate-pulse after:ml-0.5",
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ node, ...props }) => (
            <a
              {...props}
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand-400 underline hover:text-brand-300"
            />
          ),
          code: ({ node, className, children, ...props }) => {
            const isInline = !className;
            if (isInline) {
              return (
                <code
                  className="bg-bg-panel px-1.5 py-0.5 rounded text-accent text-xs"
                  {...props}
                >
                  {children}
                </code>
              );
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
