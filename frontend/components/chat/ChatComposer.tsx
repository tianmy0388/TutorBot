"use client";

/**
 * ChatComposer — text input + send button + capability selector.
 *
 * Sends messages via WebSocket through the store's actions.
 */

import { useState, useRef, useEffect } from "react";
import {
  Send,
  Loader2,
  Sparkles,
  MessageCircle,
  BarChart3,
  Compass,
  X,
} from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { sendUserMessage, cancelActiveTurn } from "@/hooks/useWebSocket";
import { cn } from "@/lib/utils";

const CAPABILITY_OPTIONS = [
  { id: "resource_generation", label: "生成资源", icon: Sparkles, hint: "例如：系统学习 LSTM" },
  { id: "tutoring", label: "即时答疑", icon: MessageCircle, hint: "例如：什么是注意力机制？" },
  { id: "assessment", label: "效果评估", icon: BarChart3, hint: "评估一下我" },
  { id: "path_planning", label: "路径规划", icon: Compass, hint: "下一步该学什么？" },
] as const;

export function ChatComposer() {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const phase = useTutorStore((s) => s.activeTurn.phase);
  const currentCapability = useTutorStore((s) => s.currentCapability);
  const setCapability = useTutorStore((s) => s.setCurrentCapability);
  const wsConnected = useTutorStore((s) => s.wsConnected);

  // Auto-resize textarea up to a max
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [text]);

  const submit = () => {
    if (!text.trim() || phase === "streaming" || phase === "connecting") return;
    sendUserMessage(text);
    setText("");
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const busy = phase === "streaming" || phase === "connecting";
  const activeCap = CAPABILITY_OPTIONS.find((c) => c.id === currentCapability);

  return (
    <div className="border-t border-fg/10 bg-bg-panel/50 backdrop-blur px-6 py-4">
      <div className="max-w-3xl mx-auto">
        {/* Capability chips */}
        <div className="flex gap-2 mb-2 flex-wrap items-center">
          {CAPABILITY_OPTIONS.map((c) => {
            const Icon = c.icon;
            const active = currentCapability === c.id;
            return (
              <button
                key={c.id}
                onClick={() => setCapability(active ? null : c.id)}
                disabled={busy}
                className={cn(
                  "px-3 py-1 rounded-full text-xs transition-colors flex items-center gap-1.5",
                  active
                    ? "bg-brand-600 text-white shadow-sm"
                    : "bg-bg-card text-fg-muted hover:text-fg hover:bg-bg/60 border border-fg/5",
                  busy && !active && "opacity-40 cursor-not-allowed",
                )}
              >
                <Icon className="w-3 h-3" />
                {c.label}
              </button>
            );
          })}
          <span className="ml-auto flex items-center gap-1.5 text-xs">
            <span
              className={cn(
                "inline-block w-2 h-2 rounded-full",
                wsConnected ? "bg-green-400 animate-pulse" : "bg-red-400",
              )}
            />
            {wsConnected ? "已连接" : "未连接"}
          </span>
        </div>

        {/* Input row */}
        <div className="flex gap-3 items-end">
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
              placeholder={
                activeCap
                  ? activeCap.hint
                  : "请输入你想学的内容… (或选择上方能力)"
              }
              rows={2}
              disabled={busy}
              className={cn(
                "w-full bg-bg-card border border-fg/10 rounded-xl px-4 py-3 resize-none",
                "focus:outline-none focus:border-brand-500 transition-colors text-sm",
                busy && "opacity-60",
              )}
            />
            {text && (
              <button
                onClick={() => setText("")}
                className="absolute right-2 top-2 p-1 text-fg-subtle hover:text-fg"
                title="清空"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {busy ? (
            <button
              onClick={() => cancelActiveTurn()}
              className="btn bg-red-900/40 hover:bg-red-900/60 text-red-200 border border-red-800/40 h-12 px-5"
              title="取消当前轮次"
            >
              <X className="w-4 h-4" />
              停止
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!text.trim()}
              className={cn(
                "btn-primary h-12 px-5",
                !text.trim() && "opacity-50 cursor-not-allowed",
              )}
              title="发送 (Enter)"
            >
              <Send className="w-4 h-4" />
              发送
            </button>
          )}
        </div>

        <p className="text-[10px] text-fg-subtle mt-2 flex items-center gap-2 flex-wrap">
          <span>Enter 发送 · Shift+Enter 换行</span>
          <span>·</span>
          <span>提示: 需要在 .env 中配置 LLM API Key</span>
          {busy && (
            <>
              <span>·</span>
              <span className="text-brand-300 inline-flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" />
                正在处理…
              </span>
            </>
          )}
        </p>
      </div>
    </div>
  );
}