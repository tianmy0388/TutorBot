"use client";

/**
 * /knowledge-bases — library manager (Task 9).
 *
 * Polls each non-terminal library every 2 seconds (single fetch, not
 * per-library) so the UI shows live ingestion progress without flooding
 * the backend.
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
import { cn } from "@/lib/utils";
import type { KnowledgeBaseDetail, KnowledgeBaseSummary } from "@/lib/types";
import { KnowledgeBaseCard } from "@/components/knowledge-base/KnowledgeBaseCard";

const POLL_INTERVAL_MS = 2000;

export default function KnowledgeBasesPage() {
  const [summaries, setSummaries] = useState<KnowledgeBaseSummary[] | null>(
    null,
  );
  const [detailsById, setDetailsById] = useState<
    Record<string, KnowledgeBaseDetail>
  >({});
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [error, setError] = useState<string | null>(null);

  const activeId = useTutorStore((s) => s.activeKnowledgeBaseId);
  const setActiveId = useTutorStore((s) => s.setActiveKnowledgeBaseId);

  const refreshAll = useCallback(async () => {
    setError(null);
    try {
      const list = await listKnowledgeBases();
      setSummaries(list.items);
      // Always refresh the details for any library that still has
      // non-terminal documents. If everything is terminal, we skip
      // the extra GETs.
      const nonTerminalIds = new Set<string>();
      // Use the existing in-memory details to avoid a flash of
      // "empty" between polls.
      for (const lib of list.items) {
        const cached = detailsById[lib.id];
        if (cached) {
          const stillWorking = cached.documents.some(
            (d) =>
              d.status !== "ready" && d.status !== "failed",
          );
          if (stillWorking) nonTerminalIds.add(lib.id);
        }
      }
      // Always include the active library so the user sees fresh state
      // when they revisit the page.
      if (activeId) nonTerminalIds.add(activeId);
      const next: Record<string, KnowledgeBaseDetail> = {};
      for (const id of nonTerminalIds) {
        try {
          next[id] = await getKnowledgeBase(id);
        } catch (e) {
          // skip — the summary list will still render
        }
      }
      setDetailsById((prev) => ({ ...prev, ...next }));
    } catch (e: any) {
      setError(e?.message ?? String(e));
    }
  }, [activeId, detailsById]);

  // Initial fetch
  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  // Poll only while at least one detail has a non-terminal document.
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
    const t = setInterval(refreshAll, POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [anyWorking, refreshAll]);

  const handleUpload = async (libId: string, file: File) => {
    await uploadKnowledgeDocument(libId, file);
    await refreshAll();
  };

  const handleRetry = async (libId: string, docId: string) => {
    await retryKnowledgeDocument(libId, docId);
    await refreshAll();
  };

  const handleDelete = async (libId: string, docId: string) => {
    await deleteKnowledgeDocument(libId, docId);
    await refreshAll();
  };

  const handleDeleteLibrary = async (libId: string) => {
    await deleteKnowledgeBase(libId);
    if (activeId === libId) setActiveId("ai_introduction");
    await refreshAll();
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    const lib = await createKnowledgeBase(newName.trim(), newDesc.trim());
    setNewName("");
    setNewDesc("");
    setCreating(false);
    setActiveId(lib.id);
    await refreshAll();
  };

  if (summaries === null) {
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
            onClick={refreshAll}
            title="刷新"
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
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          加载失败：{error}
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
