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
 *
 * 2026-06-21 plan (stage 4): the very first message of a session
 * auto-creates the server-side conversation (with the first 60 chars
 * of the question as the title). Until then the store holds a
 * transient draft id so navigation/history don't generate empty
 * "no-title" rows.
 */

import { useState, useRef, useEffect } from "react";
import { Send, Sparkles, MessageCircle, BarChart3, Compass, X, Database, ChevronDown, BookOpen } from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { useJobQueue } from "@/hooks/useJobQueue";
import { cn } from "@/lib/utils";
import {
  appendConversationMessage,
  createConversation,
  getConversation,
  listAppCourses,
  listKnowledgeBases,
  type CourseResponse,
} from "@/lib/api";
import type { KnowledgeBaseSummary } from "@/lib/types";

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
  const sessionId = useTutorStore((s) => s.sessionId);
  const currentCapability = useTutorStore((s) => s.currentCapability);
  const setCapability = useTutorStore((s) => s.setCurrentCapability);
  const addMessage = useTutorStore((s) => s.addMessage);
  const queue = useJobQueue(userId);

  // 2026-06-21 plan (D10): RAG scope selector state.
  const ragEnabled = useTutorStore((s) => s.ragEnabled);
  const retrievalScope = useTutorStore((s) => s.retrievalScope);
  const setRagEnabled = useTutorStore((s) => s.setRagEnabled);
  const setRetrievalScope = useTutorStore((s) => s.setRetrievalScope);

  // Cached course / KB lists so the dropdown doesn't fetch on
  // every render. We fetch once on mount and cache here.
  const [scopeCourses, setScopeCourses] = useState<CourseResponse[]>([]);
  const [scopeKbs, setScopeKbs] = useState<KnowledgeBaseSummary[]>([]);
  const [scopeOpen, setScopeOpen] = useState(false);

  useEffect(() => {
    if (!userId) return;
    void listAppCourses()
      .then((r) => setScopeCourses(r.items || []))
      .catch(() => {});
    void listKnowledgeBases()
      .then((r) => setScopeKbs(r.items || []))
      .catch(() => {});
  }, [userId]);

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

    // 2026-06-21 plan: the first message in a brand-new (draft) session
    // must materialise the server-side conversation before we try to
    // append the message. The store starts with a draft id (see
    // ``useTutorStore`` initial state) and only the user's first send
    // causes a row in the conversation history — that is the spec
    // behaviour for "no empty sessions in history".
    let activeSessionId = sessionId;
    if (userId && activeSessionId) {
      try {
        await getConversation(userId, activeSessionId);
      } catch (e: any) {
        if (e?.status === 404) {
          // The sessionId is a draft — promote it to a real
          // conversation now, with the first 60 chars of the
          // question as the title.
          const title = msg.length > 60 ? msg.slice(0, 60) + "…" : msg;
          const conv = await createConversation(userId, {
            session_id: activeSessionId,
            title,
          });
          activeSessionId = conv.session_id;
          useTutorStore.getState().setSessionId(conv.session_id);
        } else {
          // Network / 5xx — fall through; the append below is also
          // best-effort so a transient backend hiccup does not block
          // submission.
          console.warn("getConversation pre-flight failed", e);
        }
      }
    }

    // Persist the user message into the active conversation so the
    // sidebar's message_count updates in real time (DeepSeek-style).
    // Fire-and-forget — a failure to persist must NOT block the user
    // from submitting.
    if (userId && activeSessionId) {
      void appendConversationMessage(userId, activeSessionId, {
        role: "user",
        content: msg,
        metadata: { source: "chat_composer" },
      }).catch((e) => {
        // Swallow — UI already has the message; the persist is a
        // best-effort background write.
        console.warn("appendConversationMessage(user) failed", e);
      });
    }

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

  const scopeLabel = (() => {
    if (!ragEnabled) return "不使用知识库";
    const s = retrievalScope;
    if (!s || s.kind === "all") return "全部知识库";
    if (s.kind === "course") {
      const c = scopeCourses.find((c) => c.id === s.id);
      return c ? `课程: ${c.name}` : `课程: ${s.id}`;
    }
    if (s.kind === "library") {
      const kb = scopeKbs.find((k) => k.id === s.id);
      return kb ? `知识库: ${kb.name}` : `知识库: ${s.id}`;
    }
    return "不使用知识库";
  })();

  return (
    <div className="border-t border-fg/10 bg-bg-panel/50 backdrop-blur px-6 py-4">
      <div className="max-w-3xl mx-auto">
        {/* 2026-06-21 plan (D10): RAG scope selector */}
        <div className="flex gap-2 mb-2 flex-wrap items-center">
          <div className="relative">
            <button
              onClick={() => setScopeOpen((o) => !o)}
              disabled={submitting}
              className={cn(
                "px-2.5 py-1 rounded-full text-xs transition-colors flex items-center gap-1.5",
                ragEnabled
                  ? "bg-accent/20 text-accent border border-accent/30"
                  : "bg-bg-card text-fg-muted border border-fg/10",
              )}
            >
              <Database className="w-3 h-3" />
              {scopeLabel}
              <ChevronDown
                className={cn(
                  "w-3 h-3 transition-transform",
                  scopeOpen && "rotate-180",
                )}
              />
            </button>
            {scopeOpen && (
              <div className="absolute top-full mt-1 left-0 w-56 bg-bg-panel border border-fg/10 rounded-lg shadow-lg z-20 py-1 max-h-64 overflow-y-auto">
                {/* Disable RAG */}
                <button
                  onClick={() => {
                    setRagEnabled(false);
                    setRetrievalScope(null);
                    setScopeOpen(false);
                  }}
                  className={cn(
                    "w-full text-left px-3 py-1.5 text-xs hover:bg-bg-card flex items-center gap-2",
                    !ragEnabled && "bg-brand-500/10 text-brand-300",
                  )}
                >
                  <X className="w-3 h-3" />
                  不使用知识库
                </button>
                {/* All */}
                <button
                  onClick={() => {
                    setRagEnabled(true);
                    setRetrievalScope({ kind: "all" });
                    setScopeOpen(false);
                  }}
                  className={cn(
                    "w-full text-left px-3 py-1.5 text-xs hover:bg-bg-card flex items-center gap-2",
                    ragEnabled && retrievalScope?.kind === "all" && "bg-brand-500/10 text-brand-300",
                  )}
                >
                  <Database className="w-3 h-3" />
                  全部知识库
                </button>
                <div className="border-t border-fg/10 my-1" />
                {/* Courses */}
                {scopeCourses.length > 0 && (
                  <>
                    <div className="text-[10px] text-fg-subtle px-3 py-0.5 flex items-center gap-1">
                      <BookOpen className="w-2.5 h-2.5" />课程
                    </div>
                    {scopeCourses.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => {
                          setRagEnabled(true);
                          setRetrievalScope({ kind: "course", id: c.id });
                          setScopeOpen(false);
                        }}
                        className={cn(
                          "w-full text-left px-3 py-1.5 text-xs hover:bg-bg-card flex items-center gap-2",
                          ragEnabled &&
                            retrievalScope?.kind === "course" &&
                            retrievalScope?.id === c.id &&
                            "bg-brand-500/10 text-brand-300",
                        )}
                      >
                        <Database className="w-3 h-3 opacity-50" />
                        {c.name}
                        <span className="ml-auto text-fg-subtle text-[10px]">
                          {c.ready_count}/{c.document_count}
                        </span>
                      </button>
                    ))}
                  </>
                )}
                {/* Standalone KBs (those not in a course) */}
                {scopeKbs.length > 0 && (
                  <>
                    <div className="text-[10px] text-fg-subtle px-3 py-0.5 flex items-center gap-1">
                      <Database className="w-2.5 h-2.5" />独立知识库
                    </div>
                    {scopeKbs
                      .filter(/* show only standalone KBs */ () => true)
                      .map((kb) => (
                        <button
                          key={kb.id}
                          onClick={() => {
                            setRagEnabled(true);
                            setRetrievalScope({ kind: "library", id: kb.id });
                            setScopeOpen(false);
                          }}
                          className={cn(
                            "w-full text-left px-3 py-1.5 text-xs hover:bg-bg-card flex items-center gap-2",
                            ragEnabled &&
                              retrievalScope?.kind === "library" &&
                              retrievalScope?.id === kb.id &&
                              "bg-brand-500/10 text-brand-300",
                          )}
                        >
                          <Database className="w-3 h-3 opacity-50" />
                          {kb.name}
                          <span className="ml-auto text-fg-subtle text-[10px]">
                            {kb.ready_count}/{kb.document_count}
                          </span>
                        </button>
                      ))}
                  </>
                )}
              </div>
            )}
            {/* Click-outside to close */}
            {scopeOpen && (
              <div
                className="fixed inset-0 z-10"
                onClick={() => setScopeOpen(false)}
              />
            )}
          </div>
        </div>

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