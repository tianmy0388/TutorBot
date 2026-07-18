"use client";

/**
 * Sidebar — TutorBot's left rail (2026-07 redesign).
 *
 * Aesthetic notes:
 *   - Editorial hairline rules between sections (no heavy borders)
 *   - Capability cards feel like contents-page entries, not buttons
 *   - Conversation rows are slightly inset, with a saffron leading rule
 *     when active — quiet but unmistakable
 *   - Footer is a status strip, not a settings drawer
 *
 * Behavior is unchanged from the previous version: same store hooks,
 * same conversation API, same keyboard/click handling. This is a
 * pure visual refresh.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Plus,
  Settings,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Compass,
  MessageCircle,
  BarChart3,
  Network,
  Trash2,
  Activity,
  MessageSquare,
  Pencil,
  Loader2,
  ArrowUpRight,
} from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { useKG } from "@/hooks/useKG";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/brand/Logo";
import {
  deleteConversation,
  listConversations,
  renameConversation,
  type ConversationSummary,
} from "@/lib/api";
import type { CourseResponse } from "@/lib/types";

interface SidebarProps {
  /** May be empty during the first SSR frame — we render a placeholder. */
  sessionId: string;
  onNewSession: () => void;
  open: boolean;
  onToggle: () => void;
}

const CAPABILITY_NAV = [
  {
    id: "resource_generation",
    label: "资源生成",
    hint: "Resource",
    icon: Sparkles,
    accent: "var(--color-brand-400)",
  },
  {
    id: "tutoring",
    label: "即时答疑",
    hint: "Tutor",
    icon: MessageCircle,
    accent: "var(--color-accent)",
  },
  {
    id: "assessment",
    label: "效果评估",
    hint: "Assess",
    icon: BarChart3,
    accent: "var(--color-accent-green)",
  },
  {
    id: "path_planning",
    label: "路径规划",
    hint: "Path",
    icon: Compass,
    accent: "var(--color-accent-warm)",
  },
] as const;

export function Sidebar({ sessionId, onNewSession, open, onToggle }: SidebarProps) {
  const wsConnected = useTutorStore((s) => s.wsConnected);
  const currentCapability = useTutorStore((s) => s.currentCapability);
  const setCapability = useTutorStore((s) => s.setCurrentCapability);
  const resetSession = useTutorStore((s) => s.resetSession);
  const setSettingsOpen = useTutorStore((s) => s.setSettingsOpen);
  const userId = useTutorStore((s) => s.userId);
  const setSessionId = useTutorStore((s) => s.setSessionId);
  const loadConversationAggregate = useTutorStore(
    (s) => s.loadConversationAggregate,
  );
  const messageCount = useTutorStore((s) => s.messages.length);
  const { courses: kgCourses, currentCourse, plannedPath } = useKG();

  const [appCourses, setAppCourses] = useState<CourseResponse[]>([]);

  useEffect(() => {
    if (!userId) return;
    import("@/lib/api")
      .then((mod) => mod.listAppCourses())
      .then((r) => setAppCourses(r.items || []))
      .catch(() => {});
  }, [userId]);

  useEffect(() => {
    if (!userId) return;
    const t = setTimeout(() => {
      import("@/lib/api")
        .then((mod) => mod.listAppCourses())
        .then((r) => setAppCourses(r.items || []))
        .catch(() => {});
    }, 500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, messageCount]);

  // ---- conversation list state ----------------------------------------

  const [convs, setConvs] = useState<ConversationSummary[] | null>(null);
  const [convError, setConvError] = useState<string | null>(null);
  const [convBusy, setConvBusy] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const refreshConvs = useCallback(async () => {
    if (!userId) return;
    try {
      const r = await listConversations(userId, { limit: 50 });
      setConvs(r.items);
    } catch (e: any) {
      setConvError(e?.message ?? String(e));
    }
  }, [userId]);

  useEffect(() => {
    refreshConvs();
  }, [refreshConvs]);

  useEffect(() => {
    if (!userId) return;
    const t = setTimeout(() => {
      void refreshConvs();
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, messageCount]);

  const handleNewConv = async () => {
    if (!userId || convBusy) return;
    setConvBusy(true);
    try {
      const draftId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `s_${Date.now()}_${Math.random().toString(36).slice(2)}`;
      setSessionId(draftId);
      resetSession();
      onNewSession();
    } catch (e: any) {
      setConvError(e?.message ?? String(e));
    } finally {
      setConvBusy(false);
    }
  };

  const handleSwitchConv = async (sid: string) => {
    if (!userId || sid === sessionId) return;
    setConvBusy(true);
    try {
      setSessionId(sid);
      await loadConversationAggregate(userId, sid);
    } catch (e: any) {
      setConvError(e?.message ?? String(e));
    } finally {
      setConvBusy(false);
    }
  };

  const handleDeleteConv = async (sid: string) => {
    if (!userId || convBusy) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm("删除此对话及其所有消息？")
    ) {
      return;
    }
    setConvBusy(true);
    try {
      await deleteConversation(userId, sid);
      if (sid === sessionId) {
        setSessionId(
          typeof crypto !== "undefined" && (crypto as any).randomUUID
            ? (crypto as any).randomUUID()
            : `s_${Date.now()}_${Math.random().toString(36).slice(2)}`,
        );
        resetSession();
        onNewSession();
      }
      await refreshConvs();
    } catch (e: any) {
      setConvError(e?.message ?? String(e));
    } finally {
      setConvBusy(false);
    }
  };

  const handleStartRename = (c: ConversationSummary) => {
    setRenamingId(c.session_id);
    setRenameDraft(c.title || "");
  };

  const handleCommitRename = async (sid: string) => {
    if (!userId) return;
    const next = renameDraft.trim();
    setRenamingId(null);
    if (!next) return;
    try {
      await renameConversation(userId, sid, next);
      await refreshConvs();
    } catch (e: any) {
      setConvError(e?.message ?? String(e));
    }
  };

  // ---- render ---------------------------------------------------------

  if (!open) {
    return (
      <button
        onClick={onToggle}
        className="absolute left-3 top-3 z-20 p-2 rounded-md transition-all animate-scale-in"
        style={{
          backgroundColor: "rgb(var(--color-bg-panel))",
          border: "1px solid rgb(var(--color-rule))",
          boxShadow: "var(--shadow-soft)",
        }}
        title="展开侧栏"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    );
  }

  return (
    <>
      <button
        type="button"
        aria-label="关闭侧栏遮罩"
        onClick={onToggle}
        className="fixed inset-x-0 top-14 bottom-0 z-30 bg-black/45 md:hidden"
      />
      <aside
      className="fixed left-0 top-14 bottom-0 z-40 w-64 shrink-0 flex flex-col animate-slide-down md:static md:z-auto md:h-full"
      style={{
        backgroundColor: "rgb(var(--color-bg-subtle))",
        borderRight: "1px solid rgb(var(--color-rule) / 0.6)",
      }}
    >
      {/* Brand row inside rail — small monogram only, since AppShell
          already shows the full lockup in the top bar. Doubles as
          collapse trigger. */}
      <div
        className="h-14 px-3 flex items-center justify-between shrink-0"
        style={{ borderBottom: "1px solid rgb(var(--color-rule) / 0.6)" }}
      >
        <div className="flex items-center gap-2">
          <Logo size={22} />
          <span className="font-display text-sm font-semibold tracking-tight">
            TutorBot
          </span>
        </div>
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md text-fg-muted hover:text-fg hover:bg-bg-card transition-colors"
          title="收起侧栏"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      </div>

      {/* New conversation — full-bleed editorial button */}
      <div className="px-3 pt-3 shrink-0">
        <button
          onClick={handleNewConv}
          disabled={convBusy}
          className="btn-primary w-full h-9 text-sm"
          title="新建对话 (清空当前聊天，持久化到服务端)"
          data-testid="sidebar-conv-new-primary"
        >
          {convBusy ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Plus className="w-4 h-4" />
          )}
          <span>新建对话</span>
          <ArrowUpRight className="w-3.5 h-3.5 ml-auto opacity-60" />
        </button>
      </div>

      {/* Capability nav */}
      <div className="px-3 pt-4 pb-3 shrink-0">
        <div className="rail-label px-1 mb-2">能力 · Capability</div>
        <nav className="space-y-0.5">
          {CAPABILITY_NAV.map((c) => {
            const Icon = c.icon;
            const active = currentCapability === c.id;
            return (
              <button
                key={c.id}
                onClick={() => setCapability(active ? null : c.id)}
                className={cn(
                  "group relative w-full text-left pl-3 pr-2.5 py-2 rounded-md text-[13px] flex items-center gap-2.5",
                  "transition-all duration-150",
                  active
                    ? "text-fg"
                    : "text-fg-muted hover:text-fg",
                )}
                style={{
                  backgroundColor: active
                    ? "rgb(var(--color-bg-card))"
                    : "transparent",
                  border: active
                    ? "1px solid rgb(var(--color-rule))"
                    : "1px solid transparent",
                }}
              >
                {active && (
                  <span
                    className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r"
                    style={{ backgroundColor: c.accent }}
                  />
                )}
                <Icon
                  className={cn(
                    "w-4 h-4 shrink-0 transition-colors",
                    active ? "" : "text-fg-subtle",
                  )}
                  style={active ? { color: c.accent } : undefined}
                />
                <span className="flex-1 font-medium">{c.label}</span>
                <span
                  className="text-[9px] font-mono-tab uppercase opacity-50"
                  style={{ letterSpacing: "0.14em" }}
                >
                  {c.hint}
                </span>
              </button>
            );
          })}
        </nav>
      </div>

      <div
        className="mx-3 hr-rule shrink-0"
        style={{ borderColor: "rgb(var(--color-rule) / 0.5)" }}
      />

      {/* Course list */}
      <div className="px-3 pt-3 pb-3 shrink-0 max-h-44 overflow-y-auto">
        <div className="rail-label px-1 mb-2 flex items-center gap-1">
          <Network className="w-3 h-3" />
          课程 · Courses
        </div>
        {appCourses.length === 0 && kgCourses.length === 0 ? (
          <p className="text-xs text-fg-subtle px-2 py-1">暂无课程</p>
        ) : (
          <nav className="space-y-0.5">
            {appCourses.length > 0
              ? appCourses.map((c) => {
                  const active =
                    c.knowledge_graph_id === currentCourse ||
                    c.id === currentCourse;
                  return (
                    <button
                      key={c.id}
                      onClick={() =>
                        useTutorStore.setState({
                          currentCourse: c.knowledge_graph_id || c.id,
                        })
                      }
                      className={cn(
                        "w-full text-left px-2.5 py-1.5 rounded-md text-xs transition-colors",
                        active
                          ? "bg-bg-card text-fg"
                          : "text-fg-muted hover:text-fg hover:bg-bg-card/50",
                      )}
                    >
                      <span className="truncate block font-medium">{c.name}</span>
                      <span className="text-[9px] text-fg-subtle font-mono-tab">
                        {c.ready_count}/{c.document_count} 就绪 · {c.library_count} 库
                      </span>
                    </button>
                  );
                })
              : kgCourses.map((c) => {
                  const active = c === currentCourse;
                  return (
                    <button
                      key={c}
                      onClick={() =>
                        useTutorStore.setState({ currentCourse: c })
                      }
                      className={cn(
                        "w-full text-left px-2.5 py-1.5 rounded-md text-xs transition-colors",
                        active
                          ? "bg-bg-card text-fg"
                          : "text-fg-muted hover:text-fg hover:bg-bg-card/50",
                      )}
                    >
                      <span className="truncate block">{c}</span>
                    </button>
                  );
                })}
          </nav>
        )}
        {plannedPath && (
          <div className="mt-3 px-2 py-2 rounded-md text-[10px]"
            style={{
              backgroundColor: "rgb(var(--color-bg-card) / 0.5)",
              border: "1px solid rgb(var(--color-rule) / 0.5)",
            }}
          >
            <div className="flex items-center gap-1 mb-1 text-fg-muted">
              <Activity className="w-3 h-3" style={{ color: "var(--color-brand-400)" }} />
              当前路径
            </div>
            <div className="text-fg truncate font-medium">{plannedPath.name}</div>
            <div className="text-fg-subtle font-mono-tab">
              {plannedPath.completed_count}/{plannedPath.nodes.length} 节点
            </div>
          </div>
        )}
      </div>

      <div
        className="mx-3 hr-rule shrink-0"
        style={{ borderColor: "rgb(var(--color-rule) / 0.5)" }}
      />

      {/* Conversation history */}
      <div className="px-3 pt-3 flex-1 overflow-y-auto min-h-0">
        <div className="flex items-center justify-between mb-2 px-1">
          <div className="rail-label flex items-center gap-1">
            <MessageSquare className="w-3 h-3" />
            对话历史
          </div>
          <span
            className="text-[9px] font-mono-tab text-fg-subtle"
            data-testid="sidebar-conv-count"
            style={{ letterSpacing: "0.12em" }}
          >
            {convs?.length ?? 0}
          </span>
        </div>

        {convError && (
          <div
            className="text-[10px] text-red-300 rounded px-2 py-1 mb-2"
            style={{
              backgroundColor: "rgb(220 80 60 / 0.08)",
              border: "1px solid rgb(220 80 60 / 0.25)",
            }}
          >
            {convError}
          </div>
        )}

        {convs === null ? (
          <p className="text-xs text-fg-subtle px-2 py-1">加载中…</p>
        ) : convs.length === 0 ? (
          <p className="text-xs text-fg-subtle px-2 py-1">还没有对话</p>
        ) : (
          <ul className="space-y-0.5">
            {convs.map((c) => {
              const isActive = c.session_id === sessionId;
              const isRenaming = renamingId === c.session_id;
              return (
                <li
                  key={c.session_id}
                  className={cn(
                    "relative rounded-md transition-colors",
                    isActive
                      ? "bg-bg-card"
                      : "hover:bg-bg-card/50",
                  )}
                  style={{
                    border: isActive
                      ? "1px solid rgb(var(--color-rule))"
                      : "1px solid transparent",
                  }}
                  data-testid={`sidebar-conv-${c.session_id}`}
                >
                  {isActive && (
                    <span
                      className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r"
                      style={{ backgroundColor: "var(--color-brand-400)" }}
                    />
                  )}
                  <div className="flex items-start gap-1 pl-2.5 pr-1.5 py-1.5">
                    <button
                      onClick={() => handleSwitchConv(c.session_id)}
                      className="flex-1 min-w-0 text-left"
                      disabled={convBusy}
                      data-testid={`sidebar-conv-switch-${c.session_id}`}
                    >
                      <div className="flex items-center gap-1.5 text-fg-muted">
                        {isRenaming ? (
                          <input
                            autoFocus
                            value={renameDraft}
                            onChange={(e) => setRenameDraft(e.target.value)}
                            onBlur={() => handleCommitRename(c.session_id)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                handleCommitRename(c.session_id);
                              } else if (e.key === "Escape") {
                                setRenamingId(null);
                              }
                            }}
                            className="input text-[11px] h-5 px-1 py-0 w-full"
                            onClick={(e) => e.stopPropagation()}
                          />
                        ) : (
                          <span
                            className={cn(
                              "text-[12px] truncate",
                              isActive ? "text-fg font-medium" : "",
                            )}
                          >
                            {c.title || "(无标题)"}
                          </span>
                        )}
                      </div>
                      {!isRenaming && (
                        <div className="text-[9px] text-fg-subtle mt-0.5 font-mono-tab flex items-center gap-1"
                          style={{ letterSpacing: "0.05em" }}
                        >
                          <span>{c.message_count} 条</span>
                          <span className="opacity-50">·</span>
                          <span>{new Date(c.updated_at).toLocaleDateString()}</span>
                        </div>
                      )}
                    </button>
                    <div className="flex flex-col gap-0.5">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleStartRename(c);
                        }}
                        className="text-fg-subtle hover:text-fg p-0.5 opacity-0 group-hover:opacity-100"
                        title="重命名"
                      >
                        <Pencil className="w-3 h-3" />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteConv(c.session_id);
                        }}
                        className="text-fg-subtle hover:text-red-300 p-0.5"
                        title="删除"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Footer status strip */}
      <div
        className="px-3 py-2.5 shrink-0 flex items-center gap-2 text-xs"
        style={{
          borderTop: "1px solid rgb(var(--color-rule) / 0.6)",
          backgroundColor: "rgb(var(--color-bg-panel) / 0.5)",
        }}
      >
        <span
          className={cn(
            "inline-block w-1.5 h-1.5 rounded-full shrink-0",
            wsConnected ? "" : "",
          )}
          style={{
            backgroundColor: wsConnected
              ? "var(--color-accent-green)"
              : "rgb(220 80 60)",
            boxShadow: wsConnected
              ? "0 0 0 3px rgb(124 168 110 / 0.15)"
              : "none",
            animation: wsConnected ? "pulse 2s ease-in-out infinite" : undefined,
          }}
        />
        <span className="text-fg-muted">
          {wsConnected ? "已连接" : "未连接"}
        </span>
        <span
          className="ml-auto font-mono-tab text-[10px] text-fg-subtle truncate"
          style={{ letterSpacing: "0.05em" }}
        >
          {sessionId ? `${sessionId.slice(0, 8)}` : "—"}
        </span>
        <button
          onClick={() => setSettingsOpen(true)}
          className="p-1 text-fg-subtle hover:text-fg transition-colors"
          title="设置"
        >
          <Settings className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={() => {
            if (confirm("确定清空当前会话的所有数据吗？")) {
              resetSession();
              onNewSession();
            }
          }}
          className="p-1 text-fg-subtle hover:text-red-400 transition-colors"
          title="清空当前会话"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
      </aside>
    </>
  );
}
