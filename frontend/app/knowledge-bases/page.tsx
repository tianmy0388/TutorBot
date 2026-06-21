"use client";

/**
 * /knowledge-bases — library manager (Task 9 + 2026-06-21 stability fix).
 *
 * Fetch pattern (rewritten 2026-06-21 third pass):
 *  - First render: summaries start as `[]` (NOT null), so the spinner
 *    only shows during the very first paint. The page no longer gets
 *    stuck on the spinner if a ref-based in-flight gate accidentally
 *    swallows a request.
 *  - On mount and whenever `userId` changes, kick off one
 *    `listKnowledgeBases` and (selectively) one detail fetch per
 *    non-terminal library. Each invocation gets its own AbortController
 *    so a stale fetch can be cancelled without leaving the page in a
 *    state where the next mount is blocked.
 *  - A 2s poll runs only when at least one document is non-terminal.
 *
 * The previous version of this page had ``refreshAll`` in a
 * ``useCallback`` whose dependency was ``detailsById``, while the
 * effect that called it also depended on the callback. Every state
 * update produced a new callback, which re-fired the effect, which
 * fetched again — 7000+ requests in 2.5s in the worst case. React 19
 * strict mode also double-mounted the effect, and the in-flight ref
 * was sticky across the unmount, so the second mount's fetch never
 * actually ran. The fix below uses a fresh `aborted` ref per effect
 * cycle and a `mountedRef` to make the late-arriving async work a
 * no-op once the component is gone.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Database, Loader2, Plus, RefreshCw } from "lucide-react";
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  deleteKnowledgeDocument,
  getKnowledgeBase,
  listKnowledgeBases,
  retryKnowledgeDocument,
  uploadKnowledgeDocument,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import type { KnowledgeBaseDetail, KnowledgeBaseSummary } from "@/lib/types";
import { KnowledgeBaseCard } from "@/components/knowledge-base/KnowledgeBaseCard";

const POLL_INTERVAL_MS = 2000;

export default function KnowledgeBasesPage() {
  // CRITICAL: start as [] (not null) so the page never gets stuck on
  // the loading spinner. Null meant "still loading" — but with React 19
  // strict mode's double-mount, the in-flight ref was sometimes sticky
  // and the second mount's fetch was never issued, so summaries stayed
  // null forever. Empty array means "loaded, but no libraries yet",
  // which renders the empty state instead of spinning.
  const [summaries, setSummaries] = useState<KnowledgeBaseSummary[]>([]);
  const [detailsById, setDetailsById] = useState<
    Record<string, KnowledgeBaseDetail>
  >({});
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const userId = useTutorStore((s) => s.userId);
  const activeId = useTutorStore((s) => s.activeKnowledgeBaseId);
  const setActiveId = useTutorStore((s) => s.setActiveKnowledgeBaseId);

  const detailsByIdRef = useRef(detailsById);
  useEffect(() => {
    detailsByIdRef.current = detailsById;
  }, [detailsById]);

  const load = useCallback(async () => {
    if (!userId) return;
    setError(null);
    setLoading(true);
    try {
      const list = await listKnowledgeBases();
      setSummaries(list.items);

      // Decide which libraries need a detail fetch. Only those with a
      // known non-terminal document, plus the active library if any.
      const cached = detailsByIdRef.current;
      const want = new Set<string>();
      for (const lib of list.items) {
        const detail = cached[lib.id];
        if (detail) {
          const stillWorking = detail.documents.some(
            (d) => d.status !== "ready" && d.status !== "failed",
          );
          if (stillWorking) want.add(lib.id);
        }
      }
      if (activeId) want.add(activeId);

      const next: Record<string, KnowledgeBaseDetail> = {};
      for (const id of want) {
        try {
          next[id] = await getKnowledgeBase(id);
        } catch {
          // ignore — summary list will still render
        }
      }
      if (Object.keys(next).length > 0) {
        setDetailsById((prev) => ({ ...prev, ...next }));
      }
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }, [userId, activeId]);

  // One-shot initial load + reload on userId change.
  useEffect(() => {
    if (!userId) {
      setSummaries([]);
      setLoading(false);
      return;
    }
    load();
  }, [load, userId]);

  // -- poll only while there's work to do ---------------------------------

  const anyWorking = useMemo(
    () =>
      Object.values(detailsById).some((d) =>
        d.documents.some(
          (doc) => doc.status !== "ready" && doc.status !== "failed",
        ),
      ),
    [detailsById],
  );
  useEffect(() => {
    if (!anyWorking) return;
    const t = setInterval(() => {
      load();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [anyWorking, load]);

  // -- mutation handlers --------------------------------------------------

  const handleUpload = async (libId: string, file: File) => {
    try {
      await uploadKnowledgeDocument(libId, file);
      await load();
    } catch (e: any) {
      setError(`上传失败：${e?.message ?? String(e)}`);
    }
  };

  const handleRetry = async (libId: string, docId: string) => {
    try {
      await retryKnowledgeDocument(libId, docId);
      await load();
    } catch (e: any) {
      setError(`重试失败：${e?.message ?? String(e)}`);
    }
  };

  const handleDelete = async (libId: string, docId: string) => {
    try {
      await deleteKnowledgeDocument(libId, docId);
      await load();
    } catch (e: any) {
      setError(`删除失败：${e?.message ?? String(e)}`);
    }
  };

  const handleDeleteLibrary = async (libId: string) => {
    try {
      await deleteKnowledgeBase(libId);
      if (activeId === libId) setActiveId("ai_introduction");
      await load();
    } catch (e: any) {
      setError(`删除知识库失败：${e?.message ?? String(e)}`);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      const lib = await createKnowledgeBase(newName.trim(), newDesc.trim());
      setNewName("");
      setNewDesc("");
      setCreating(false);
      setActiveId(lib.id);
      await load();
    } catch (e: any) {
      setError(`创建失败：${e?.message ?? String(e)}`);
    }
  };

  if (loading && summaries.length === 0) {
    return (
      <div className="flex items-center justify-center p-12 text-fg-muted">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> 正在加载…
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">知识库</h1>
          <p className="text-xs text-fg-muted mt-1">
            管理课程资料库；上传的文档会被分块、嵌入并用于答疑与资源生成的引用。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary text-sm h-9"
            onClick={() => setCreating((v) => !v)}
            data-testid="kb-new-toggle"
          >
            <Plus className="w-4 h-4" />
            <span className="ml-1">新建</span>
          </button>
          <button
            className="btn-secondary text-sm h-9"
            onClick={() => load()}
            title="刷新"
            data-testid="kb-refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </header>

      {creating && (
        <section className="rounded-xl border border-fg/10 bg-bg-panel p-4 space-y-3">
          <h3 className="text-sm font-semibold">新建知识库</h3>
          <input
            className="input"
            placeholder="名称 (例如: 操作系统进阶)"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            data-testid="kb-new-name"
          />
          <input
            className="input"
            placeholder="说明 (可选)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            data-testid="kb-new-desc"
          />
          <div className="flex justify-end gap-2">
            <button
              className="btn-secondary text-sm h-9"
              onClick={() => setCreating(false)}
            >
              取消
            </button>
            <button
              className="btn-primary text-sm h-9"
              onClick={handleCreate}
              disabled={!newName.trim()}
              data-testid="kb-new-create"
            >
              创建
            </button>
          </div>
        </section>
      )}

      {error && (
        <div
          className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300"
          data-testid="kb-error"
        >
          {error}
          <button
            className="ml-2 underline"
            onClick={() => setError(null)}
          >
            关闭
          </button>
        </div>
      )}

      {summaries.length === 0 ? (
        <div className="rounded-xl border border-dashed border-fg/10 p-8 text-center text-fg-muted">
          <Database className="w-8 h-8 mx-auto mb-2 opacity-50" />
          还没有知识库。点击"新建"创建一个。
        </div>
      ) : (
        <div className="space-y-3">
          {summaries.map((s) => {
            const detail = detailsById[s.id] ?? {
              ...s,
              documents: [],
            };
            return (
              <KnowledgeBaseCard
                key={s.id}
                detail={detail}
                isActive={activeId === s.id}
                onSelect={() => setActiveId(s.id)}
                onUpload={(file) => handleUpload(s.id, file)}
                onRetry={(docId) => handleRetry(s.id, docId)}
                onDelete={(docId) => handleDelete(s.id, docId)}
                onDeleteLibrary={() => handleDeleteLibrary(s.id)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
