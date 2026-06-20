"use client";

/**
 * ResourceTray — sidebar list of all resources in the latest package.
 *
 * Features:
 *  - Group by type with section headers + counts
 *  - Filter by type (chip selector)
 *  - Sort by type/difficulty/duration/confidence
 *  - Empty state with prompt
 *  - Click selects resource (rendered by parent in detail pane)
 */

import { useState, useMemo } from "react";
import {
  Sparkles,
  Package,
  ChevronDown,
  Filter,
  ArrowUpDown,
} from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { ResourceCard, RESOURCE_TYPE_META } from "./ResourceCard";
import { cn } from "@/lib/utils";

type SortBy = "default" | "difficulty" | "duration" | "confidence";
type FilterType = "all" | string;

export function ResourceTray() {
  const latestPackage = useTutorStore((s) => s.latestPackage);
  const selection = useTutorStore((s) => s.resourceSelection);
  const select = useTutorStore((s) => s.selectResource);

  const [filter, setFilter] = useState<FilterType>("all");
  const [sortBy, setSortBy] = useState<SortBy>("default");
  const [sortOpen, setSortOpen] = useState(false);

  const totalMinutes = useMemo(() => {
    if (!latestPackage) return 0;
    return latestPackage.resources.reduce(
      (s, r) => s + (r.estimated_minutes || 0),
      0,
    );
  }, [latestPackage]);

  const presentTypes = useMemo(() => {
    if (!latestPackage) return [];
    const set = new Set(latestPackage.resources.map((r) => r.type));
    return Array.from(set);
  }, [latestPackage]);

  const visibleResources = useMemo(() => {
    if (!latestPackage) return [];
    let arr = [...latestPackage.resources];
    if (filter !== "all") arr = arr.filter((r) => r.type === filter);
    switch (sortBy) {
      case "difficulty":
        arr.sort((a, b) => a.difficulty - b.difficulty);
        break;
      case "duration":
        arr.sort((a, b) => a.estimated_minutes - b.estimated_minutes);
        break;
      case "confidence":
        arr.sort((a, b) => b.confidence_score - a.confidence_score);
        break;
    }
    return arr;
  }, [latestPackage, filter, sortBy]);

  if (!latestPackage || latestPackage.resources.length === 0) {
    return (
      <div className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-accent" />
            资源中心
          </h2>
        </div>
        <div className="text-center py-8 text-xs text-fg-muted">
          <Package className="w-8 h-8 mx-auto mb-2 opacity-40" />
          <p>暂无资源</p>
          <p className="mt-1 text-fg-subtle">发送"系统学习 XXX"开始生成</p>
        </div>
      </div>
    );
  }

  // Group by type for compact display (preserves filter)
  const byType = new Map<string, typeof latestPackage.resources>();
  for (const r of visibleResources) {
    const arr = byType.get(r.type) || [];
    arr.push(r);
    byType.set(r.type, arr);
  }

  return (
    <div className="p-5 border-t border-fg/10 h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 shrink-0">
        <h2 className="font-semibold flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-accent" />
          资源中心
        </h2>
        <span className="text-[10px] text-fg-muted">
          {latestPackage.resources.length} 项 · {totalMinutes} 分
        </span>
      </div>
      <div className="text-[11px] text-fg-muted mb-3 truncate">
        📦 {latestPackage.topic}
      </div>

      {/* Filter chips */}
      <div className="flex gap-1 mb-2 flex-wrap text-[10px] shrink-0">
        <button
          onClick={() => setFilter("all")}
          className={cn(
            "px-2 py-0.5 rounded-md transition-colors",
            filter === "all"
              ? "bg-brand-600/30 text-brand-200"
              : "text-fg-muted hover:text-fg bg-bg-panel",
          )}
        >
          全部 ({latestPackage.resources.length})
        </button>
        {presentTypes.map((t) => {
          const meta = RESOURCE_TYPE_META[t as keyof typeof RESOURCE_TYPE_META];
          const count = latestPackage.resources.filter(
            (r) => r.type === t,
          ).length;
          if (!meta) return null;
          const Icon = meta.icon;
          return (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={cn(
                "px-2 py-0.5 rounded-md transition-colors flex items-center gap-1",
                filter === t
                  ? "bg-brand-600/30 text-brand-200"
                  : "text-fg-muted hover:text-fg bg-bg-panel",
              )}
            >
              <Icon className="w-3 h-3" />
              {meta.label} ({count})
            </button>
          );
        })}
      </div>

      {/* Sort */}
      <div className="flex items-center gap-2 mb-3 shrink-0">
        <Filter className="w-3 h-3 text-fg-subtle" />
        <span className="text-[10px] text-fg-subtle">排序</span>
        <div className="relative">
          <button
            onClick={() => setSortOpen((s) => !s)}
            className="text-[10px] px-2 py-0.5 rounded-md bg-bg-panel text-fg-muted hover:text-fg flex items-center gap-1"
          >
            <ArrowUpDown className="w-2.5 h-2.5" />
            {sortBy === "default"
              ? "默认"
              : sortBy === "difficulty"
              ? "难度 ↑"
              : sortBy === "duration"
              ? "时长 ↑"
              : "置信度 ↓"}
            <ChevronDown className="w-2.5 h-2.5" />
          </button>
          {sortOpen && (
            <div className="absolute top-full left-0 mt-1 z-10 bg-bg-card border border-fg/10 rounded-md shadow-lg text-xs overflow-hidden">
              {(
                [
                  ["default", "默认"],
                  ["difficulty", "难度 ↑"],
                  ["duration", "时长 ↑"],
                  ["confidence", "置信度 ↓"],
                ] as [SortBy, string][]
              ).map(([k, label]) => (
                <button
                  key={k}
                  onClick={() => {
                    setSortBy(k);
                    setSortOpen(false);
                  }}
                  className={cn(
                    "block w-full text-left px-3 py-1.5 hover:bg-bg-panel",
                    sortBy === k && "text-brand-300",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Resource list */}
      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {Array.from(byType.entries()).map(([type, items]) => {
          const meta = RESOURCE_TYPE_META[type as keyof typeof RESOURCE_TYPE_META];
          return (
            <div key={type}>
              <div
                className={cn(
                  "text-[11px] font-semibold mb-1.5 flex items-center gap-1.5 px-1",
                  meta?.color || "text-fg-muted",
                )}
              >
                {meta && <meta.icon className="w-3 h-3" />}
                {meta?.label || type}
                <span className="text-fg-subtle ml-auto">{items.length}</span>
              </div>
              <div className="space-y-1.5">
                {items.map((r) => (
                  <ResourceCard
                    key={r.resource_id}
                    resource={r}
                    compact
                    selected={selection.selectedResourceId === r.resource_id}
                    onClick={() => select(r.resource_id)}
                  />
                ))}
              </div>
            </div>
          );
        })}
        {visibleResources.length === 0 && (
          <div className="text-center py-6 text-xs text-fg-muted">
            没有匹配的资源
          </div>
        )}
      </div>
    </div>
  );
}