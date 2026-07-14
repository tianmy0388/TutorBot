"use client";

/**
 * ConversationSidebar — minimal history list (2026-06-21 plan, stage 4).
 *
 * Displays persisted conversations for the active user, lets them
 * start a new one, switch into a previous one, rename, and delete.
 * The chat store keeps a ``sessionId``; switching replaces it and
 * the chat surface re-subscribes through the existing job pipeline.
 *
 * Scope of this first cut:
 *   - One-shot list on mount + after each mutation (no polling).
 *   - New conversation creates an empty session on the server and
 *     switches the chat over to it.
 *   - Switch loads the conversation's messages into the chat store
 *     so the user sees their previous turns.
 *   - Delete cascades server-side; the local session clears.
 *
 * Out of scope (deferred to a follow-up plan):
 *   - Infinite scroll / "load more"
 *   - Group by recency buckets (today / yesterday / earlier)
 *   - Optimistic UI for rename / delete
 */

import { useCallback, useEffect, useState } from "react";
import {
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import type { ConversationSummary } from "@/lib/api";
import { MessageSquare, Plus, Trash2, Pencil, Loader2 } from "lucide-react";

export function ConversationSidebar() {
  const userId = useTutorStore((s) => s.userId);
  const sessionId = useTutorStore((s) => s.sessionId);
  const setSessionId = useTutorStore((s) => s.setSessionId);
  // 2026-06-21 plan: switching history must restore the right pane,
  // not just the messages — use the aggregate loader.
  const loadConversationAggregate = useTutorStore(
    (s) => s.loadConversationAggregate,
  );
  const resetSession = useTutorStore((s) => s.resetSession);

  const [items, setItems] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const refresh = useCallback(async () => {
    if (!userId) return;
    try {
      const r = await listConversations(userId, { limit: 50 });
      setItems(r.items);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  }, [userId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleNew = async () => {
    if (!userId || busy) return;
    setBusy(true);
    try {
      const conv = await createConversation(userId, {});
      setSessionId(conv.session_id);
      resetSession();
      await refresh();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleSwitch = async (sid: string) => {
    if (!userId || sid === sessionId) return;
    setBusy(true);
    try {
      setSessionId(sid);
      await loadConversationAggregate(userId, sid);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (sid: string) => {
    if (!userId || busy) return;
    if (
      typeof window !== "undefined" &&
      !window.confirm("删除此对话及其所有消息？")
    ) {
      return;
    }
    setBusy(true);
    try {
      await deleteConversation(userId, sid);
      if (sid === sessionId) {
        setSessionId(
          typeof crypto !== "undefined" && (crypto as any).randomUUID
            ? (crypto as any).randomUUID()
            : `s_${Date.now()}_${Math.random().toString(36).slice(2)}`,
        );
        resetSession();
      }
      await refresh();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleStartRename = (conv: ConversationSummary) => {
    setRenamingId(conv.session_id);
    setRenameDraft(conv.title || "");
  };

  const handleCommitRename = async (sid: string) => {
    if (!userId) return;
    const next = renameDraft.trim();
    setRenamingId(null);
    if (!next) return;
    try {
      await renameConversation(userId, sid, next);
      await refresh();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  };

  return (
    <aside
      className="w-60 shrink-0 border-r border-fg/10 bg-bg-panel/50 p-3 flex flex-col gap-2 h-full"
      data-testid="conversation-sidebar"
    >
      <button
        onClick={handleNew}
        disabled={busy}
        className="btn-primary text-sm h-9 flex items-center justify-center gap-1"
        data-testid="conversation-new"
      >
        {busy ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Plus className="w-4 h-4" />
        )}
        <span>新建对话</span>
      </button>

      {error && (
        <div
          className="text-[11px] text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-1"
          data-testid="conversation-error"
        >
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto -mx-1">
        {items === null ? (
          <div className="text-[11px] text-fg-muted px-2 py-1">加载中…</div>
        ) : items.length === 0 ? (
          <div className="text-[11px] text-fg-muted px-2 py-1">
            还没有对话
          </div>
        ) : (
          <ul className="space-y-1">
            {items.map((c) => {
              const isActive = c.session_id === sessionId;
              const isRenaming = renamingId === c.session_id;
              return (
                <li
                  key={c.session_id}
                  className={
                    "rounded-lg border " +
                    (isActive
                      ? "border-brand-500/40 bg-brand-500/10"
                      : "border-transparent hover:border-fg/10 hover:bg-bg-panel")
                  }
                  data-testid={`conversation-item-${c.session_id}`}
                >
                  <div className="flex items-start gap-1 p-2">
                    <button
                      onClick={() => handleSwitch(c.session_id)}
                      className="flex-1 min-w-0 text-left"
                      disabled={busy}
                      data-testid={`conversation-switch-${c.session_id}`}
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
                            className="input text-xs h-6 px-1 py-0 w-full"
                            onClick={(e) => e.stopPropagation()}
                            data-testid={`conversation-rename-input-${c.session_id}`}
                          />
                        ) : (
                          <span className="text-xs font-medium truncate">
                            {c.title || "(无标题)"}
                          </span>
                        )}
                      </div>
                      {!isRenaming && (
                        <div className="text-[10px] text-fg-subtle mt-0.5 truncate">
                          {c.message_count} 条消息 ·{" "}
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
                        data-testid={`conversation-rename-${c.session_id}`}
                      >
                        <Pencil className="w-3 h-3" />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(c.session_id);
                        }}
                        className="text-fg-muted hover:text-red-300 p-0.5"
                        title="删除"
                        data-testid={`conversation-delete-${c.session_id}`}
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
    </aside>
  );
}

export default ConversationSidebar;
