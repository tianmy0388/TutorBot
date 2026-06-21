"use client";

/**
 * Sidebar — session, capability, course, **and conversation history**
 * rail (2026-06-21 — conversation history added in stage 4).
 *
 *  - Top: new session + collapse
 *  - Capability switcher
 *  - Course picker (KG)
 *  - Conversation history (persisted via /conversations)
 *  - Footer: WS status, session id
 *
 * The conversation history portion is the inlined version of
 * ``ConversationSidebar`` — we keep it here so users can see past
 * sessions, switch between them, rename, and delete in one rail.
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
} from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { useKG } from "@/hooks/useKG";
import { cn } from "@/lib/utils";
import {
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
  type ConversationSummary,
} from "@/lib/api";

interface SidebarProps {
  /** May be empty during the first SSR frame — we render a placeholder. */
  sessionId: string;
  onNewSession: () => void;
  open: boolean;
  onToggle: () => void;
}

const CAPABILITY_NAV = [
  { id: "resource_generation", label: "资源生成", icon: Sparkles, color: "text-accent" },
  { id: "tutoring", label: "即时答疑", icon: MessageCircle, color: "text-brand-300" },
  { id: "assessment", label: "效果评估", icon: BarChart3, color: "text-green-400" },
  { id: "path_planning", label: "路径规划", icon: Compass, color: "text-yellow-300" },
] as const;

export function Sidebar({ sessionId, onNewSession, open, onToggle }: SidebarProps) {
  const wsConnected = useTutorStore((s) => s.wsConnected);
  const currentCapability = useTutorStore((s) => s.currentCapability);
  const setCapability = useTutorStore((s) => s.setCurrentCapability);
  // Back-compat alias in case the store is later renamed.
  const setCurrentCapability = useTutorStore((s) => s.setCurrentCapability);
  const resetSession = useTutorStore((s) => s.resetSession);
  const setSettingsOpen = useTutorStore((s) => s.setSettingsOpen);
  const userId = useTutorStore((s) => s.userId);
  const setSessionId = useTutorStore((s) => s.setSessionId);
  const loadConversationIntoStore = useTutorStore(
    (s) => s.loadConversationIntoStore,
  );
  const { courses, currentCourse, plannedPath } = useKG();

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

  const handleNewConv = async () => {
    if (!userId || convBusy) return;
    setConvBusy(true);
    try {
      const conv = await createConversation(userId, {});
      setSessionId(conv.session_id);
      resetSession();
      onNewSession();
      await refreshConvs();
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
      await loadConversationIntoStore(userId, sid);
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
        className="absolute left-2 top-2 z-10 p-2 bg-bg-panel border border-fg/10 rounded-lg hover:bg-bg-card transition-colors shadow-md"
        title="展开侧栏"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    );
  }

  return (
    <aside className="w-64 bg-bg-panel border-r border-fg/10 flex flex-col h-full">
      {/* Top: new session + collapse */}
      <div className="p-3 border-b border-fg/10 flex items-center justify-between shrink-0">
        <button
          onClick={() => {
            resetSession();
            onNewSession();
          }}
          className="btn-ghost flex-1 mr-2 text-sm"
          title="开始新会话 (清空当前聊天历史)"
          data-testid="sidebar-new-session"
        >
          <Plus className="w-4 h-4" />
          新会话
        </button>
        <button
          onClick={onToggle}
          className="p-2 hover:bg-bg-card rounded-lg text-fg-muted hover:text-fg transition-colors"
          title="收起侧栏"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      </div>

      {/* Capability nav */}
      <div className="p-3 border-b border-fg/10 shrink-0">
        <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold mb-2 px-1">
          能力
        </div>
        <nav className="space-y-1">
          {CAPABILITY_NAV.map((c) => {
            const Icon = c.icon;
            const active = currentCapability === c.id;
            return (
              <button
                key={c.id}
                onClick={() => setCapability(active ? null : c.id)}
                className={cn(
                  "w-full text-left px-3 py-2 rounded-lg text-sm flex items-center gap-2 transition-colors",
                  active
                    ? "bg-brand-600/30 text-brand-200 border border-brand-500/40"
                    : "hover:bg-bg-card text-fg-muted hover:text-fg",
                )}
              >
                <Icon className={cn("w-4 h-4", active && c.color)} />
                <span className="flex-1">{c.label}</span>
                {active && <span className="w-1.5 h-1.5 rounded-full bg-brand-400" />}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Course list (KG) */}
      <div className="p-3 border-b border-fg/10 shrink-0 max-h-40 overflow-y-auto">
        <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold mb-2 px-1 flex items-center gap-1">
          <Network className="w-3 h-3" />
          课程
        </div>
        {courses.length === 0 ? (
          <p className="text-xs text-fg-subtle px-2 py-1">暂无课程</p>
        ) : (
          <nav className="space-y-0.5">
            {courses.map((c) => {
              const active = c === currentCourse;
              return (
                <button
                  key={c}
                  onClick={() =>
                    useTutorStore.setState({ currentCourse: c })
                  }
                  className={cn(
                    "w-full text-left px-3 py-1.5 rounded-md text-xs transition-colors",
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
          <div className="mt-3 px-1 text-[10px] text-fg-muted">
            <div className="flex items-center gap-1 mb-1">
              <Activity className="w-3 h-3" />
              当前路径
            </div>
            <div className="text-fg truncate">{plannedPath.name}</div>
            <div className="text-fg-subtle">
              {plannedPath.completed_count}/{plannedPath.nodes.length} 节点
            </div>
          </div>
        )}
      </div>

      {/* Conversation history (2026-06-21 plan, stage 4) */}
      <div className="p-3 border-b border-fg/10 flex-1 overflow-y-auto min-h-0">
        <div className="flex items-center justify-between mb-2 px-1">
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold flex items-center gap-1">
            <MessageSquare className="w-3 h-3" />
            对话历史
          </div>
          <button
            onClick={handleNewConv}
            disabled={convBusy}
            className="text-[10px] text-brand-300 hover:text-brand-200 disabled:opacity-50 flex items-center gap-0.5"
            data-testid="sidebar-conv-new"
            title="新建对话"
          >
            {convBusy ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Plus className="w-3 h-3" />
            )}
            新建
          </button>
        </div>

        {convError && (
          <div className="text-[10px] text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-1 mb-2">
            {convError}
          </div>
        )}

        {convs === null ? (
          <p className="text-xs text-fg-subtle px-2 py-1">加载中…</p>
        ) : convs.length === 0 ? (
          <p className="text-xs text-fg-subtle px-2 py-1">还没有对话</p>
        ) : (
          <ul className="space-y-1">
            {convs.map((c) => {
              const isActive = c.session_id === sessionId;
              const isRenaming = renamingId === c.session_id;
              return (
                <li
                  key={c.session_id}
                  className={cn(
                    "rounded-lg border",
                    isActive
                      ? "border-brand-500/40 bg-brand-500/10"
                      : "border-transparent hover:border-fg/10 hover:bg-bg-card",
                  )}
                  data-testid={`sidebar-conv-${c.session_id}`}
                >
                  <div className="flex items-start gap-1 p-2">
                    <button
                      onClick={() => handleSwitchConv(c.session_id)}
                      className="flex-1 min-w-0 text-left"
                      disabled={convBusy}
                      data-testid={`sidebar-conv-switch-${c.session_id}`}
                    >
                      <div className="flex items-center gap-1 text-fg-muted">
                        <MessageSquare className="w-3 h-3 shrink-0" />
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
                            className="input text-[10px] h-5 px-1 py-0 w-full"
                            onClick={(e) => e.stopPropagation()}
                          />
                        ) : (
                          <span className="text-[11px] font-medium truncate">
                            {c.title || "(无标题)"}
                          </span>
                        )}
                      </div>
                      {!isRenaming && (
                        <div className="text-[9px] text-fg-subtle mt-0.5 truncate">
                          {c.message_count} 条 ·{" "}
                          {new Date(c.updated_at).toLocaleDateString()}
                        </div>
                      )}
                    </button>
                    <div className="flex flex-col gap-0.5">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleStartRename(c);
                        }}
                        className="text-fg-muted hover:text-fg p-0.5"
                        title="重命名"
                      >
                        <Pencil className="w-3 h-3" />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteConv(c.session_id);
                        }}
                        className="text-fg-muted hover:text-red-300 p-0.5"
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

      {/* Footer */}
      <div className="p-3 border-t border-fg/10 shrink-0">
        <div className="flex items-center gap-2 text-xs text-fg-muted mb-2">
          <span
            className={cn(
              "inline-block w-2 h-2 rounded-full shrink-0",
              wsConnected ? "bg-green-400 animate-pulse" : "bg-red-400",
            )}
          />
          <span>{wsConnected ? "WebSocket 已连接" : "WebSocket 未连接"}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <div className="font-mono text-[10px] text-fg-subtle truncate flex-1">
            {sessionId ? `${sessionId.slice(0, 8)}…` : "connecting…"}
          </div>
          <button
            onClick={() => setSettingsOpen(true)}
            className="p-1.5 text-fg-subtle hover:text-fg transition-colors"
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
            className="p-1.5 text-fg-subtle hover:text-red-400 transition-colors"
            title="清空会话"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </aside>
  );
}
