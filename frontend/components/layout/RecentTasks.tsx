"use client";

/**
 * RecentTasks — 你的空间 / 最近任务 (2026-07-19 plan).
 *
 * Lists the 8 most recent conversations; clicking one switches the
 * active session (setSessionId + loadConversationAggregate) and routes
 * to /workspace. Row delete cascades server-side (messages, resource
 * packages, job rows); deleting the active session mirrors
 * workspace/page.tsx's startNewTask (fresh id + resetSession).
 */

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { History, Trash2 } from "lucide-react";

import {
  deleteConversation,
  listConversations,
  type ConversationSummary,
} from "@/lib/api";
import { formatRelativeTime } from "@/lib/format-time";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function RecentTasks() {
  const router = useRouter();
  const pathname = usePathname() || "/";
  const userId = useTutorStore((s) => s.userId);
  const sessionId = useTutorStore((s) => s.sessionId);
  const setSessionId = useTutorStore((s) => s.setSessionId);
  const resetSession = useTutorStore((s) => s.resetSession);
  const loadConversationAggregate = useTutorStore(
    (s) => s.loadConversationAggregate,
  );
  const [tasks, setTasks] = useState<ConversationSummary[]>([]);
  const [openingId, setOpeningId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!userId) return;
    try {
      const response = await listConversations(userId, { limit: 8 });
      setTasks(response.items || []);
    } catch {
      // Best-effort sidebar list; keep the last good snapshot on error.
    }
  }, [userId]);

  // Mount + lightweight refresh on route change.
  useEffect(() => {
    void refresh();
  }, [refresh, pathname]);

  const openTask = async (task: ConversationSummary) => {
    if (!userId || openingId) return;
    setOpeningId(task.session_id);
    try {
      if (task.session_id !== sessionId) {
        setSessionId(task.session_id);
        await loadConversationAggregate(userId, task.session_id);
      }
      if (!pathname.startsWith("/workspace")) {
        router.push("/workspace");
      }
    } catch {
      // 打开失败：停留在当前页，列表保持不变。
    } finally {
      setOpeningId(null);
    }
  };

  const removeTask = async (task: ConversationSummary) => {
    if (!userId) return;
    const label = task.title || task.last_message_preview || "未命名任务";
    if (!window.confirm(`删除任务「${label}」？该操作不可恢复。`)) return;
    try {
      await deleteConversation(userId, task.session_id);
    } catch {
      return;
    }
    if (task.session_id === sessionId) {
      // Mirror startNewTask: fresh session id, cleared panels/messages.
      setSessionId(window.crypto.randomUUID());
      resetSession();
    }
    await refresh();
  };

  if (tasks.length === 0) return null;

  return (
    <div className="mt-6" data-testid="recent-tasks">
      <div className="px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-subtle">
        最近任务
      </div>
      <ul className="mt-2 space-y-1">
        {tasks.map((task) => {
          const label = task.title || task.last_message_preview || "未命名任务";
          return (
            <li key={task.session_id} className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void openTask(task)}
                disabled={openingId === task.session_id}
                className={cn(
                  "flex min-h-11 min-w-0 flex-1 items-center gap-2 rounded-2xl px-3 text-left text-sm transition-colors",
                  task.session_id === sessionId
                    ? "bg-bg-subtle text-fg"
                    : "text-fg-muted hover:bg-bg-subtle hover:text-fg",
                )}
              >
                <History className="h-[16px] w-[16px] shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{label}</span>
                  <span className="block text-[11px] text-fg-subtle">
                    {formatRelativeTime(task.updated_at)}
                  </span>
                </span>
              </button>
              <button
                type="button"
                aria-label={`删除任务 ${label}`}
                onClick={() => void removeTask(task)}
                className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
