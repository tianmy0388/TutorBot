"use client";

/**
 * ChatComposer — text input + capability selector + send button.
 *
 * Phase 5.2: Uses the async submit_job flow via useJobQueue so the
 * UI does not block while a capability runs in the background.
 *
 * After submit:
 *   - User message is added to chat history immediately
 *   - A JobTray entry appears (status: pending → running)
 *   - The user can keep typing / submit more jobs in parallel
 *   - When the job completes, the appropriate right-side panel
 *     populates automatically (via dispatchStreamEvent → event-handler)
 */

import { useState, useRef, useEffect } from "react";
import { Send, Sparkles, MessageCircle, BarChart3, Compass, X } from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { useJobQueue } from "@/hooks/useJobQueue";
import { cn } from "@/lib/utils";

const CAPABILITY_OPTIONS = [
  { id: "resource_generation", label: "生成资源", icon: Sparkles, hint: "例如:系统学习 LSTM" },
  { id: "tutoring", label: "即时答疑", icon: MessageCircle, hint: "例如:什么是注意力机制?" },
  { id: "assessment", label: "效果评估", icon: BarChart3, hint: "评估一下我" },
  { id: "path_planning", label: "路径规划", icon: Compass, hint: "下一步该学什么?" },
] as const;

export function ChatComposer() {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const userId = useTutorStore((s) => s.userId);
  const currentCapability = useTutorStore((s) => s.currentCapability);
  const setCapability = useTutorStore((s) => s.setCurrentCapability);
  const addMessage = useTutorStore((s) => s.addMessage);
  const queue = useJobQueue(userId);

  // Auto-resize textarea up to a max
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [text]);

  const submit = async () => {
    if (!text.trim() || submitting) return;
    const msg = text.trim();
    setText("");
    setSubmitting(true);

    // Add user message to chat immediately
    addMessage({ role: "user", content: msg });

    try {
      const result = await queue.submit(msg, currentCapability || undefined);
      if (result) {
        // Subscribe to the job's live event stream. This drives the
        // chat-message pipeline via dispatchStreamEvent so the existing
        // event-handler does the rest.
        queue.subscribe(result.job_id, result.capability);
      }
    } catch (e: any) {
      console.error("submit failed", e);
      addMessage({
        role: "system",
        content: `提交失败: ${e?.message || e}`,
        metadata: { source: "chat_composer" },
      });
    } finally {
      setSubmitting(false);
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const activeCap = CAPABILITY_OPTIONS.find((c) => c.id === currentCapability);
  const activeCount = queue.activeJobs.length;

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
                disabled={submitting}
                className={cn(
                  "px-3 py-1 rounded-full text-xs transition-colors flex items-center gap-1.5",
                  active
                    ? "bg-brand-600 text-white shadow-sm"
                    : "bg-bg-card text-fg-muted hover:text-fg hover:bg-bg/60 border border-fg/5",
                  submitting && !active && "opacity-40 cursor-not-allowed",
                )}
              >
                <Icon className="w-3 h-3" />
                {c.label}
              </button>
            );
          })}
          {activeCount > 0 && (
            <span className="ml-auto text-[10px] text-brand-300 flex items-center gap-1">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-brand-400 animate-pulse" />
              {activeCount} 任务运行中
            </span>
          )}
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
              disabled={submitting}
              className={cn(
                "w-full bg-bg-card border border-fg/10 rounded-xl px-4 py-3 resize-none",
                "focus:outline-none focus:border-brand-500 transition-colors text-sm",
                submitting && "opacity-60",
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

          <button
            onClick={submit}
            disabled={!text.trim() || submitting}
            className={cn(
              "btn-primary h-12 px-5",
              (!text.trim() || submitting) && "opacity-50 cursor-not-allowed",
            )}
            title="发送 (Enter) — 异步执行,不阻塞 UI"
          >
            <Send className="w-4 h-4" />
            发送
          </button>
        </div>

        <p className="text-[10px] text-fg-subtle mt-2 flex items-center gap-2 flex-wrap">
          <span>Enter 发送 · Shift+Enter 换行</span>
          <span>·</span>
          <span>异步执行:发送后可继续输入,右上角"任务"查看进度</span>
        </p>
      </div>
    </div>
  );
}