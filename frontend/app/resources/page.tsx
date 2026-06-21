"use client";

/**
 * /resources — persisted resource center (Task 10).
 *
 * Lists resource packages with filters and lets the user preview or
 * download individual resources.
 */

import { useEffect, useState } from "react";
import { Loader2, BookOpen, Filter } from "lucide-react";
import {
  listResourcePackages,
  getResourcePackageDetail,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import { ResourceDetail } from "@/components/resources/ResourceCard";
import type {
  ResourcePackage,
  ResourcePackageSummary,
  ResourceType,
} from "@/lib/types";

const TYPE_LABELS: Record<ResourceType, string> = {
  document: "文档",
  mindmap: "思维导图",
  exercise: "练习",
  reading: "阅读",
  video: "视频",
  code: "代码",
  ppt: "PPT",
};

export default function ResourcesPage() {
  const userId = useTutorStore((s) => s.userId);
  const [items, setItems] = useState<ResourcePackageSummary[] | null>(null);
  const [typeFilter, setTypeFilter] = useState<ResourceType | "all">("all");
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ResourcePackage | null>(null);

  useEffect(() => {
    listResourcePackages(userId)
      .then((r) => setItems(r.items))
      .catch((e) => setError(e?.message ?? String(e)));
  }, [userId]);

  if (items === null) {
    return (
      <div className="flex items-center justify-center p-12 text-fg-muted">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> 正在加载资源…
      </div>
    );
  }

  const filtered = items.filter(
    (p) => typeFilter === "all" || (p.types ?? []).includes(typeFilter),
  );

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 space-y-5">
      <header>
        <h1 className="text-xl font-semibold">资源中心</h1>
        <p className="text-xs text-fg-muted mt-1">
          历次生成的学习资源包；按类型筛选并预览。
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          加载失败：{error}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <Filter className="w-4 h-4 text-fg-muted" />
        <button
          className={`btn-secondary text-xs h-7 ${typeFilter === "all" ? "ring-1 ring-brand-500" : ""}`}
          onClick={() => setTypeFilter("all")}
        >
          全部
        </button>
        {(Object.keys(TYPE_LABELS) as ResourceType[]).map((t) => (
          <button
            key={t}
            className={`btn-secondary text-xs h-7 ${typeFilter === t ? "ring-1 ring-brand-500" : ""}`}
            onClick={() => setTypeFilter(t)}
          >
            {TYPE_LABELS[t]}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-xl border border-dashed border-fg/10 p-8 text-center text-fg-muted">
          <BookOpen className="w-8 h-8 mx-auto mb-2 opacity-50" />
          还没有资源。
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((p) => (
            <button
              key={p.package_id}
              onClick={async () => {
                try {
                  const d = await getResourcePackageDetail(userId, p.package_id);
                  setPreview(d);
                } catch (e) {
                  setError(String(e));
                }
              }}
              className="w-full text-left rounded-xl border border-fg/10 bg-bg-panel p-3 hover:border-brand-500/40 transition-colors"
              data-testid={`resource-card-${p.package_id}`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="font-medium text-sm truncate">
                  {p.topic}
                </div>
                <div className="text-[11px] text-fg-muted shrink-0">
                  {new Date(p.created_at).toLocaleString()}
                </div>
              </div>
              <div className="text-[11px] text-fg-muted mt-1">
                {p.resource_count} 项 · {(p.types ?? []).map((t) => TYPE_LABELS[t as ResourceType] ?? t).join("、") || "—"} · 平均置信度 {Math.round((p.avg_confidence ?? 0) * 100)}%
              </div>
            </button>
          ))}
        </div>
      )}

      {preview && (
        <ResourcePackagePreview
          pkg={preview}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Two-pane package preview
// ---------------------------------------------------------------------------

function ResourcePackagePreview({
  pkg,
  onClose,
}: {
  pkg: ResourcePackage;
  onClose: () => void;
}) {
  // Selected resource id; defaults to the first item so the detail
  // pane is never empty when there is at least one resource.
  const [selectedResourceId, setSelectedResourceId] = useState<string | null>(
    pkg.resources[0]?.resource_id ?? null,
  );
  const selected =
    pkg.resources.find((r) => r.resource_id === selectedResourceId) ??
    pkg.resources[0] ??
    null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-5xl max-h-[85vh] overflow-hidden rounded-xl border border-fg/10 bg-bg-panel flex flex-col"
        onClick={(e) => e.stopPropagation()}
        data-testid="resource-package-preview"
      >
        <header className="px-5 py-3 border-b border-fg/10 flex items-center justify-between gap-2">
          <div>
            <h3 className="text-base font-semibold">{pkg.topic}</h3>
            <p className="text-xs text-fg-muted mt-0.5">
              {pkg.resources.length} 项资源
            </p>
          </div>
          <button
            className="btn-secondary text-sm h-9"
            onClick={onClose}
            data-testid="resource-preview-close"
          >
            关闭
          </button>
        </header>

        <div className="flex-1 grid grid-cols-1 md:grid-cols-[260px_1fr] min-h-0">
          {/* Left: resource list as accessible buttons */}
          <nav
            className="border-r border-fg/10 overflow-y-auto p-2 space-y-1"
            aria-label="资源列表"
            data-testid="resource-list"
          >
            {pkg.resources.map((r) => {
              const isSelected = r.resource_id === selected.resource_id;
              return (
                <button
                  key={r.resource_id}
                  type="button"
                  onClick={() => setSelectedResourceId(r.resource_id)}
                  className={
                    "w-full text-left rounded-lg border px-3 py-2 transition-colors " +
                    (isSelected
                      ? "border-brand-500/50 bg-brand-500/10"
                      : "border-transparent hover:border-fg/10 hover:bg-bg-card")
                  }
                  aria-pressed={isSelected}
                  data-testid={`resource-list-item-${r.resource_id}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-sm truncate">
                      {r.title || r.type}
                    </span>
                    <span className="text-[10px] text-fg-muted shrink-0">
                      {Math.round(r.confidence_score * 100)}%
                    </span>
                  </div>
                  <div className="text-[10px] text-fg-muted mt-0.5 truncate">
                    {TYPE_LABELS[r.type as ResourceType] ?? r.type} ·{" "}
                    {r.estimated_minutes} 分钟
                  </div>
                </button>
              );
            })}
          </nav>

          {/* Right: detail viewer */}
          <section
            className="overflow-y-auto p-4 min-h-0"
            data-testid="resource-detail"
          >
            {selected ? (
              <ResourceDetail resource={selected} />
            ) : (
              <div className="text-sm text-fg-muted">未选择资源</div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
