"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDownAZ, BookOpen, Grid2X2, List, Loader2, Search, X } from "lucide-react";
import { getResourcePackageDetail, listResourcePackages } from "@/lib/api";
import { ResourceDetail } from "@/components/resources/ResourceCard";
import { useTutorStore } from "@/lib/store";
import type { ResourcePackage, ResourcePackageSummary, ResourceType } from "@/lib/types";
import { cn, userFacingError } from "@/lib/utils";

const TYPE_LABELS: Record<ResourceType, string> = {
  document: "讲解",
  mindmap: "思维导图",
  exercise: "练习",
  reading: "阅读",
  video: "视频",
  code: "代码",
  ppt: "演示文稿",
};

type ViewMode = "gallery" | "list";
type SortMode = "newest" | "name";

export default function ResourcesPage() {
  const userId = useTutorStore((state) => state.userId);
  const [items, setItems] = useState<ResourcePackageSummary[] | null>(null);
  const [preview, setPreview] = useState<ResourcePackage | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<ResourceType | "all">("all");
  const [sort, setSort] = useState<SortMode>("newest");
  const [view, setView] = useState<ViewMode>("gallery");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listResourcePackages(userId)
      .then(async (response) => {
        if (cancelled) return;
        setItems(response.items);
        const packageId = new URLSearchParams(window.location.search).get("package");
        if (packageId) await openPackage(packageId, true);
      })
      .catch((cause) => {
        if (cancelled) return;
        setError(userFacingError(cause, "资料暂时无法读取，请稍后重试。"));
        setItems([]);
      });
    return () => { cancelled = true; };
    // userId is the persistence boundary; openPackage intentionally stays local.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const openPackage = async (packageId: string, preserveResource = false) => {
    setPreviewLoading(true);
    try {
      const detail = await getResourcePackageDetail(userId, packageId);
      setPreview(detail);
      setError(null);
      const url = new URL(window.location.href);
      url.searchParams.set("package", packageId);
      if (!preserveResource) url.searchParams.delete("resource");
      window.history.replaceState(null, "", `${url.pathname}${url.search}`);
    } catch (cause) {
      setError(userFacingError(cause, "资料详情暂时无法打开，请稍后重试。"));
    } finally {
      setPreviewLoading(false);
    }
  };

  const closePreview = () => {
    setPreview(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("package");
    url.searchParams.delete("resource");
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  };

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return [...(items ?? [])]
      .filter((item) => typeFilter === "all" || (item.types ?? []).includes(typeFilter))
      .filter((item) => !normalized || item.topic.toLowerCase().includes(normalized))
      .sort((left, right) => sort === "name" ? left.topic.localeCompare(right.topic, "zh-CN") : new Date(right.created_at).getTime() - new Date(left.created_at).getTime());
  }, [items, query, sort, typeFilter]);

  if (items === null) return <div className="knowledge-canvas flex h-full items-center justify-center text-fg-muted"><Loader2 className="mr-2 h-5 w-5 animate-spin" />正在打开最近资料…</div>;

  return (
    <div className="knowledge-canvas h-full overflow-y-auto">
      <header className="mx-auto max-w-[1480px] px-5 pb-5 pt-8 sm:px-8 lg:px-10 lg:pt-10">
        <div className="text-4xl" aria-hidden="true">🗂️</div>
        <h1 className="mt-4 font-display text-4xl font-bold tracking-[-0.03em] sm:text-5xl">最近资料</h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-fg-muted">学习过程中整理出的讲解、练习和阅读内容。打开一组资料后，可以在右侧逐项阅读。</p>

        {error && <div className="mt-5 rounded-lg border border-border bg-bg-panel px-4 py-3 text-sm">资料加载未完成：{error}</div>}

        <div className="mt-8 flex flex-wrap items-center gap-2 border-b border-border pb-3">
          <label className="flex min-h-10 min-w-[220px] flex-1 items-center gap-2 rounded-md bg-bg-panel px-3 text-sm text-fg-muted sm:max-w-sm"><Search className="h-4 w-4" /><input value={query} onChange={(event) => setQuery(event.target.value)} className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-fg-subtle" placeholder="查找学习主题" /></label>
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value as ResourceType | "all")} className="input min-h-10 w-auto py-0 text-xs" aria-label="资料类型"><option value="all">全部类型</option>{(Object.keys(TYPE_LABELS) as ResourceType[]).map((type) => <option key={type} value={type}>{TYPE_LABELS[type]}</option>)}</select>
          <button type="button" onClick={() => setSort((value) => value === "newest" ? "name" : "newest")} className="flex min-h-10 items-center gap-2 rounded-md bg-bg-panel px-3 text-xs text-fg-muted hover:text-fg"><ArrowDownAZ className="h-4 w-4" />{sort === "newest" ? "最近更新" : "按名称"}</button>
          <div className="ml-auto flex rounded-md border border-border bg-bg-panel p-0.5"><button className={cn("flex min-h-9 min-w-9 items-center justify-center rounded", view === "gallery" && "bg-bg-subtle")} onClick={() => setView("gallery")} aria-label="图库视图"><Grid2X2 className="h-4 w-4" /></button><button className={cn("flex min-h-9 min-w-9 items-center justify-center rounded", view === "list" && "bg-bg-subtle")} onClick={() => setView("list")} aria-label="列表视图"><List className="h-4 w-4" /></button></div>
        </div>
      </header>

      <div className={cn("mx-auto grid max-w-[1480px] gap-8 px-5 pb-12 sm:px-8 lg:px-10", preview && "lg:grid-cols-[minmax(0,0.88fr)_minmax(480px,1.12fr)]")}>
        {filtered.length === 0 ? (
          <div className="flex min-h-[360px] flex-col items-center justify-center text-center text-fg-muted"><BookOpen className="h-8 w-8" /><p className="mt-4 text-sm font-semibold text-fg">还没有符合条件的资料</p><p className="mt-2 text-xs">在学习任务中整理内容后，会保存在这里。</p></div>
        ) : (
          <section className={cn("content-start", view === "gallery" ? "grid gap-3 sm:grid-cols-2" : "space-y-1")} aria-label="学习资料列表">
            {filtered.map((item) => <PackageItem key={item.package_id} item={item} view={view} selected={preview?.package_id === item.package_id} loading={previewLoading && preview?.package_id !== item.package_id} onClick={() => void openPackage(item.package_id)} />)}
          </section>
        )}

        {preview && (
          <aside className="fixed inset-0 z-[60] min-h-0 overflow-hidden bg-bg-panel lg:sticky lg:top-6 lg:z-auto lg:h-[calc(100vh-48px)] lg:rounded-lg lg:border lg:border-border" aria-label="资料详情">
            <ResourcePackagePreview pkg={preview} onClose={closePreview} />
          </aside>
        )}
      </div>
    </div>
  );
}

function PackageItem({ item, view, selected, loading, onClick }: { item: ResourcePackageSummary; view: ViewMode; selected: boolean; loading: boolean; onClick: () => void }) {
  const types = item.types ?? [];
  return (
    <button type="button" onClick={onClick} className={cn("group w-full border border-border bg-bg-panel text-left transition-all duration-200 hover:border-fg-subtle", view === "gallery" ? "min-h-[200px] rounded-lg p-5" : "flex min-h-16 items-center gap-4 rounded-md px-4 py-3", selected && "ring-2 ring-fg ring-offset-2 ring-offset-[rgb(var(--knowledge-bg))]")} data-testid={`resource-card-${item.package_id}`}>
      <span className={cn("flex shrink-0 items-center justify-center bg-bg-subtle", view === "gallery" ? "h-11 w-11 rounded-lg text-xl" : "h-9 w-9 rounded text-sm")} aria-hidden="true">📄</span>
      <span className="min-w-0 flex-1"><span className={cn("block truncate font-semibold", view === "gallery" && "mt-6 text-base")}>{item.topic}</span><span className="mt-2 block text-xs text-fg-muted">{item.resource_count} 项 · {item.total_minutes} 分钟</span><span className={cn("block truncate text-[11px] text-fg-muted", view === "gallery" ? "mt-4" : "mt-1")}>{types.map((type) => TYPE_LABELS[type as ResourceType] ?? type).join("、") || "内容整理中"}</span></span>
      {loading && <Loader2 className="h-4 w-4 animate-spin text-fg-muted" />}
    </button>
  );
}

function ResourcePackagePreview({ pkg, onClose }: { pkg: ResourcePackage; onClose: () => void }) {
  const fromUrl = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("resource") : null;
  const [selectedResourceId, setSelectedResourceId] = useState<string | null>(fromUrl || pkg.resources[0]?.resource_id || null);
  const selected = pkg.resources.find((resource) => resource.resource_id === selectedResourceId) ?? pkg.resources[0] ?? null;

  const selectResource = (resourceId: string) => {
    setSelectedResourceId(resourceId);
    const url = new URL(window.location.href);
    url.searchParams.set("resource", resourceId);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  };

  return (
    <div className="flex h-full flex-col" data-testid="resource-package-preview">
      <header className="flex min-h-16 shrink-0 items-center justify-between gap-3 border-b border-border px-4 sm:px-5"><div className="min-w-0"><h2 className="truncate font-display text-xl font-semibold">{pkg.topic}</h2><p className="mt-1 text-xs text-fg-muted">{pkg.resources.length} 项学习资料</p></div><button type="button" className="flex min-h-10 min-w-10 items-center justify-center rounded-full text-fg-muted hover:bg-bg-subtle hover:text-fg" onClick={onClose} data-testid="resource-preview-close" aria-label="关闭资料详情"><X className="h-4 w-4" /></button></header>
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[220px_minmax(0,1fr)]">
        <nav className="max-h-52 overflow-y-auto border-b border-border bg-bg-subtle p-2 md:max-h-none md:border-b-0 md:border-r" aria-label="资料列表" data-testid="resource-list">{pkg.resources.map((resource) => { const active = resource.resource_id === selected?.resource_id; return <button key={resource.resource_id} type="button" onClick={() => selectResource(resource.resource_id)} className={cn("mb-1 w-full rounded-md px-3 py-2.5 text-left transition-colors", active ? "bg-bg-panel" : "hover:bg-bg-panel/70")} aria-pressed={active} data-testid={`resource-list-item-${resource.resource_id}`}><span className="block truncate text-sm font-medium">{resource.title || TYPE_LABELS[resource.type]}</span><span className="mt-1 block text-[11px] text-fg-muted">{TYPE_LABELS[resource.type]} · {resource.estimated_minutes} 分钟</span></button>; })}</nav>
        <section className="min-h-0 overflow-y-auto" data-testid="resource-detail">{selected ? <ResourceDetail resource={selected} /> : <div className="p-6 text-sm text-fg-muted">选择一项资料开始阅读。</div>}</section>
      </div>
    </div>
  );
}
