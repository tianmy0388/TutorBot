"use client";

/**
 * /knowledge-bases — library manager (Task 9 + 2026-06-21 stability fix).
 *
 * The page is split into three concerns so it doesn't get into a
 * render loop:
 *
 *  - load(): one-shot list + selective detail fetch, run on mount
 *    and on manual refresh.
 *  - tick(): a 2s poll that ONLY runs when at least one document is
 *    non-terminal, and uses refs to read the latest state without
 *    re-creating the callback.
 *  - mutation handlers (upload / retry / delete / create): each
 *    catches errors and surfaces them in-page instead of producing
 *    unhandled rejections.
 *
 * The previous version of this page had ``refreshAll`` in a
 * ``useCallback`` whose dependency was ``detailsById``, while the
 * effect that called it depended on the callback. Every state update
 * produced a new callback, which re-fired the effect, which fetched
 * again — 7000+ requests in 2.5s in the worst case. The fix below
 * uses refs and a single state transition per fetch.
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

  // Refs let callbacks read the latest state without re-creating
  // themselves — that breaks the render loop the old version had.
  const detailsByIdRef = useRef(detailsById);
  const activeIdRef = useRef(activeId);
  const inFlightRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    detailsByIdRef.current = detailsById;
  }, [detailsById]);
  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  // -- core fetch ----------------------------------------------------------

  const load = useCallback(async () => {
    if (inFlightRef.current) return; // dedupe overlapping requests
    inFlightRef.current = true;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setError(null);
    try {
      const list = await listKnowledgeBases();
      if (ac.signal.aborted) return;
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
      if (activeIdRef.current) want.add(activeIdRef.current);

      const next: Record<string, KnowledgeBaseDetail> = {};
      for (const id of want) {
        try {
          next[id] = await getKnowledgeBase(id);
          if (ac.signal.aborted) return;
        } catch {
          // ignore — summary list will still render
        }
      }
      if (Object.keys(next).length === 0) return;
      setDetailsById((prev) => ({ ...prev, ...next }));
    } catch (e: any) {
      if (!ac.signal.aborted) {
        setError(e?.message ?? String(e));
      }
    } finally {
      inFlightRef.current = false;
    }
  }, []); // stable identity, no deps

  // One-shot initial load.
  useEffect(() => {
    load();
    return () => abortRef.current?.abort();
  }, [load]);

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
