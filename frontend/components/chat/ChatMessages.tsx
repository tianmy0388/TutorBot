"use client";

/**
 * ChatMessages — render chat history + live streaming output.
 *
 * Features:
 *  - Auto-scroll on new content
 *  - Streaming markdown with blinking caret
 *  - Per-source agent badge
 *  - Stage indicator + step progress for active turn
 *  - Collapsible thinking + trace timeline
 *  - Tool call/result chip rendering
 *  - Empty state with quick-start suggestion cards
 */

import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import { StageIndicator, StageRow } from "./StageIndicator";
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
  const activeTurn = useTutorStore((s) => s.activeTurn);
  const tracePanelOpen = useTutorStore((s) => s.tracePanelOpen);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to bottom on new content
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [
    messages.length,
    activeTurn.text_buffer,
    activeTurn.thinking_buffer,
    activeTurn.events.length,
  ]);

  return (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto px-6 py-6 space-y-5"
    >
      {messages.length === 0 && activeTurn.phase === "idle" ? (
        <EmptyState />
      ) : (
        messages.map((m) => <MessageBubble key={m.id} message={m} />)
      )}

      {activeTurn.phase !== "idle" && (
        <ActiveTurnView
          textBuffer={activeTurn.text_buffer}
          thinkingBuffer={activeTurn.thinking_buffer}
          stage={
            activeTurn.events
              .filter((e) => e.type === "stage_start")
              .slice(-1)[0]?.stage || ""
          }
          showTrace={tracePanelOpen}
          events={activeTurn.events}
          error={activeTurn.error}
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
  return (
    <div className="max-w-3xl mr-auto animate-fade-in">
      <div className="card bg-bg-panel/80 border-fg/10">
        {/* Stage header */}
        <StageIndicator currentStage={props.stage} />

        {/* Pipeline stage progress */}
        {props.showTrace && props.events.length > 0 && (
          <StageProgress events={props.events} />
        )}

        {/* Thinking (collapsible) */}
        {props.thinkingBuffer && props.showTrace && (
          <details className="mt-3">
            <summary className="text-xs text-fg-muted cursor-pointer hover:text-fg transition-colors">
              💭 思考过程 ({props.thinkingBuffer.length} 字符)
            </summary>
            <pre className="mt-2 text-xs text-fg-muted whitespace-pre-wrap max-h-48 overflow-y-auto bg-bg/60 rounded-lg p-3">
              {props.thinkingBuffer}
            </pre>
          </details>
        )}

        {/* Live text */}
        {props.textBuffer && (
          <div className="mt-3">
            <MarkdownContent content={props.textBuffer} streaming />
          </div>
        )}

        {/* Loading placeholder */}
        {!props.textBuffer && !props.thinkingBuffer && !props.error && (
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
  const starts = events.filter((e) => e.type === "stage_start");
  const ends = events.filter((e) => e.type === "stage_end");
  const active = starts[starts.length - 1];
  if (!active) return null;

  const seen = new Set<string>();
  const sequence = starts
    .map((s) => s.stage)
    .filter((s) => {
      if (seen.has(s)) return false;
      seen.add(s);
      return true;
    });

  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[10px]">
      {sequence.map((stage, i) => {
        const isActive = stage === active.stage;
        const isDone = !isActive && ends.some((e) => e.stage === stage);
        return (
          <div key={`${stage}_${i}`} className="flex items-center gap-1">
            <StageRow stage={stage} state={isDone ? "done" : isActive ? "active" : "pending"} />
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