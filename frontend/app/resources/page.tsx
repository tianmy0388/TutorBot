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
    (p) => typeFilter === "all" || p.types.includes(typeFilter),
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
                {p.resource_count} 项 · {p.types.map((t) => TYPE_LABELS[t as ResourceType] ?? t).join("、")} · 平均置信度 {Math.round(p.avg_confidence * 100)}%
              </div>
            </button>
          ))}
        </div>
      )}

      {preview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
          onClick={() => setPreview(null)}
        >
          <div
            className="w-full max-w-2xl max-h-[80vh] overflow-auto rounded-xl border border-fg/10 bg-bg-panel p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold">{preview.topic}</h3>
            <p className="text-xs text-fg-muted mt-1">
              {preview.resources.length} 项资源
            </p>
            <ul className="mt-3 space-y-1">
              {preview.resources.map((r) => (
                <li
                  key={r.resource_id}
                  className="rounded-lg border border-fg/10 bg-bg-card p-3 text-sm"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{r.title || r.type}</span>
                    <span className="text-[11px] text-fg-muted">
                      置信度 {Math.round(r.confidence_score * 100)}%
                    </span>
                  </div>
                  <div className="text-[11px] text-fg-muted mt-0.5">
                    {TYPE_LABELS[r.type as ResourceType] ?? r.type} · {r.estimated_minutes} 分钟
                  </div>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex justify-end">
              <button
                className="btn-secondary text-sm h-9"
                onClick={() => setPreview(null)}
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
