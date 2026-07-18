"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Database, Grid2X2, List, Loader2, Plus, RefreshCw, Search } from "lucide-react";
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  deleteKnowledgeDocument,
  getKnowledgeBase,
  listKnowledgeBases,
  retryKnowledgeDocument,
  uploadKnowledgeDocument,
} from "@/lib/api";
import { KnowledgeBaseCard } from "@/components/knowledge-base/KnowledgeBaseCard";
import { useTutorStore } from "@/lib/store";
import type { KnowledgeBaseDetail, KnowledgeBaseSummary } from "@/lib/types";
import { cn, userFacingError } from "@/lib/utils";

const POLL_INTERVAL_MS = 2000;
type ViewMode = "gallery" | "list";

export default function KnowledgeBasesPage() {
  const [summaries, setSummaries] = useState<KnowledgeBaseSummary[]>([]);
  const [detailsById, setDetailsById] = useState<Record<string, KnowledgeBaseDetail>>({});
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<ViewMode>("gallery");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const userId = useTutorStore((state) => state.userId);
  const activeId = useTutorStore((state) => state.activeKnowledgeBaseId);
  const setActiveId = useTutorStore((state) => state.setActiveKnowledgeBaseId);
  const detailsByIdRef = useRef(detailsById);

  useEffect(() => { detailsByIdRef.current = detailsById; }, [detailsById]);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("library");
    if (id) setActiveId(id);
  }, [setActiveId]);

  const selectLibrary = useCallback((id: string) => {
    setActiveId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("library", id);
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, [setActiveId]);

  const load = useCallback(async () => {
    if (!userId) return;
    setError(null);
    setLoading(true);
    try {
      const list = await listKnowledgeBases();
      setSummaries(list.items);
      const cached = detailsByIdRef.current;
      const wanted = new Set<string>();
      for (const library of list.items) {
        const detail = cached[library.id];
        if (detail?.documents.some((document) => document.status !== "ready" && document.status !== "failed")) wanted.add(library.id);
      }
      if (activeId) wanted.add(activeId);

      const next: Record<string, KnowledgeBaseDetail> = {};
      for (const id of wanted) {
        try { next[id] = await getKnowledgeBase(id); } catch { /* summary remains usable */ }
      }
      if (Object.keys(next).length) setDetailsById((previous) => ({ ...previous, ...next }));
    } catch (cause) {
      setError(userFacingError(cause, "资料库暂时不可用，请稍后重试。"));
    } finally {
      setLoading(false);
    }
  }, [activeId, userId]);

  useEffect(() => {
    if (!userId) { setSummaries([]); setLoading(false); return; }
    void load();
  }, [load, userId]);

  const anyWorking = useMemo(
    () => Object.values(detailsById).some((detail) => detail.documents.some((document) => document.status !== "ready" && document.status !== "failed")),
    [detailsById],
  );

  useEffect(() => {
    if (!anyWorking) return;
    const timer = window.setInterval(() => { void load(); }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [anyWorking, load]);

  const mutate = async (action: () => Promise<unknown>, fallback: string) => {
    try { await action(); await load(); } catch (cause) { setError(`${fallback}：${userFacingError(cause)}`); }
  };
  const handleUpload = (libraryId: string, file: File) => mutate(() => uploadKnowledgeDocument(libraryId, file), "上传失败");
  const handleRetry = (libraryId: string, documentId: string) => mutate(() => retryKnowledgeDocument(libraryId, documentId), "重试失败");
  const handleDelete = (libraryId: string, documentId: string) => mutate(() => deleteKnowledgeDocument(libraryId, documentId), "删除失败");
  const handleDeleteLibrary = (libraryId: string) => mutate(async () => {
    await deleteKnowledgeBase(libraryId);
    if (activeId === libraryId) selectLibrary("ai_introduction");
  }, "删除资料库失败");

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      const library = await createKnowledgeBase(newName.trim(), newDesc.trim());
      setNewName(""); setNewDesc(""); setCreating(false); selectLibrary(library.id); await load();
    } catch (cause) { setError(`创建失败：${userFacingError(cause)}`); }
  };

  const filtered = summaries.filter((summary) => `${summary.name} ${summary.description}`.toLowerCase().includes(query.trim().toLowerCase()));
  const selectedSummary = summaries.find((summary) => summary.id === activeId) ?? summaries[0] ?? null;
  const selectedDetail = selectedSummary ? detailsById[selectedSummary.id] ?? { ...selectedSummary, documents: [] } : null;

  if (loading && summaries.length === 0) {
    return <div className="knowledge-canvas flex h-full items-center justify-center text-fg-muted"><Loader2 className="mr-2 h-5 w-5 animate-spin" />正在打开资料库…</div>;
  }

  return (
    <div className="knowledge-canvas h-full overflow-y-auto">
      <header className="mx-auto max-w-[1480px] px-5 pb-5 pt-8 sm:px-8 lg:px-10 lg:pt-10">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div>
            <div className="text-4xl" aria-hidden="true">📚</div>
            <h1 className="mt-4 font-display text-4xl font-bold tracking-[-0.03em] sm:text-5xl">资料库</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-fg-muted">把课程讲义、阅读材料和自己的笔记放在一起。学习时只会在你选择的范围内查找。</p>
          </div>
          <button className="btn-primary min-h-11" onClick={() => setCreating((value) => !value)} data-testid="kb-new-toggle"><Plus className="h-4 w-4" />新建资料库</button>
        </div>

        {creating && (
          <section className="mt-6 rounded-lg border border-border bg-bg-panel p-4">
            <div className="grid gap-3 md:grid-cols-[1fr_1.4fr_auto] md:items-end">
              <label className="text-xs text-fg-muted">名称<input className="input mt-1" placeholder="例如：操作系统进阶" value={newName} onChange={(event) => setNewName(event.target.value)} data-testid="kb-new-name" /></label>
              <label className="text-xs text-fg-muted">说明<input className="input mt-1" placeholder="可选" value={newDesc} onChange={(event) => setNewDesc(event.target.value)} data-testid="kb-new-desc" /></label>
              <div className="flex gap-2"><button className="btn-secondary min-h-10" onClick={() => setCreating(false)}>取消</button><button className="btn-primary min-h-10" onClick={handleCreate} disabled={!newName.trim()} data-testid="kb-new-create">创建</button></div>
            </div>
          </section>
        )}

        {error && <div className="mt-4 rounded-lg border border-border bg-bg-panel px-4 py-3 text-sm" data-testid="kb-error">资料库操作未完成：{error}<button className="ml-2 underline" onClick={() => void load()}>重新加载</button></div>}

        <div className="mt-8 flex flex-wrap items-center gap-2 border-b border-border pb-3">
          <label className="flex min-h-10 min-w-[220px] flex-1 items-center gap-2 rounded-md bg-bg-panel px-3 text-sm text-fg-muted sm:max-w-sm"><Search className="h-4 w-4" /><input className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-fg-subtle" placeholder="查找资料库" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          <span className="text-xs text-fg-muted">{filtered.length} 个资料库</span>
          <div className="ml-auto flex rounded-md border border-border bg-bg-panel p-0.5">
            <button className={cn("flex min-h-9 min-w-9 items-center justify-center rounded", view === "gallery" && "bg-bg-subtle")} onClick={() => setView("gallery")} aria-label="图库视图"><Grid2X2 className="h-4 w-4" /></button>
            <button className={cn("flex min-h-9 min-w-9 items-center justify-center rounded", view === "list" && "bg-bg-subtle")} onClick={() => setView("list")} aria-label="列表视图"><List className="h-4 w-4" /></button>
          </div>
          <button className="flex min-h-10 min-w-10 items-center justify-center rounded-md text-fg-muted hover:bg-bg-panel hover:text-fg" onClick={() => void load()} title="刷新" data-testid="kb-refresh"><RefreshCw className="h-4 w-4" /></button>
        </div>
      </header>

      {summaries.length === 0 ? (
        <div className="mx-auto flex max-w-xl flex-col items-center px-6 py-24 text-center text-fg-muted"><Database className="mb-4 h-8 w-8" /><p className="font-semibold text-fg">还没有资料库</p><p className="mt-2 text-sm">新建一个资料库，再放入课程文档。</p></div>
      ) : (
        <div className="mx-auto grid max-w-[1480px] gap-8 px-5 pb-12 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.72fr)] lg:px-10">
          <section className={cn("min-w-0", view === "gallery" ? "grid content-start gap-3 sm:grid-cols-2 xl:grid-cols-3" : "space-y-1")} aria-label="资料库列表">
            {filtered.map((summary) => <LibraryItem key={summary.id} summary={summary} selected={selectedSummary?.id === summary.id} view={view} onSelect={() => selectLibrary(summary.id)} />)}
          </section>
          <aside className="knowledge-detail min-w-0 rounded-lg border border-border bg-bg-panel p-5 lg:sticky lg:top-6 lg:max-h-[calc(100vh-48px)] lg:overflow-y-auto sm:p-6">
            {selectedDetail && selectedSummary ? <KnowledgeBaseCard key={selectedSummary.id} detail={selectedDetail} isActive={activeId === selectedSummary.id || !activeId} onSelect={() => selectLibrary(selectedSummary.id)} onUpload={(file) => handleUpload(selectedSummary.id, file)} onRetry={(id) => handleRetry(selectedSummary.id, id)} onDelete={(id) => handleDelete(selectedSummary.id, id)} onDeleteLibrary={() => handleDeleteLibrary(selectedSummary.id)} /> : <p className="text-sm text-fg-muted">选择一个资料库查看详情。</p>}
          </aside>
        </div>
      )}
    </div>
  );
}

function LibraryItem({ summary, selected, view, onSelect }: { summary: KnowledgeBaseSummary; selected: boolean; view: ViewMode; onSelect: () => void }) {
  return (
    <button type="button" onClick={onSelect} className={cn("group w-full border border-border bg-bg-panel text-left transition-all duration-200 hover:border-fg-subtle", view === "gallery" ? "min-h-[190px] rounded-lg p-5" : "flex min-h-14 items-center gap-4 rounded-md px-4 py-3", selected && "ring-2 ring-fg ring-offset-2 ring-offset-[rgb(var(--knowledge-bg))]")}>
      <span className={cn("flex shrink-0 items-center justify-center bg-bg-subtle", view === "gallery" ? "h-11 w-11 rounded-lg text-xl" : "h-8 w-8 rounded text-sm")} aria-hidden="true">📖</span>
      <span className="min-w-0 flex-1">
        <span className={cn("block truncate font-semibold", view === "gallery" && "mt-6 text-base")}>{summary.name}</span>
        {summary.description && <span className="mt-1 block line-clamp-2 text-xs leading-5 text-fg-muted">{summary.description}</span>}
        <span className={cn("block text-[11px] text-fg-muted", view === "gallery" ? "mt-4" : "mt-1")}>{summary.ready_count}/{summary.document_count} 就绪 · {summary.total_chunks} 个内容块</span>
      </span>
    </button>
  );
}
