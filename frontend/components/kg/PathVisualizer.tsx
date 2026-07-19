"use client";

/**
 * PathVisualizer — sequential list of planned learning path nodes
 * with status badges, progress bar, and per-node expansion.
 */

import { useState } from "react";
import {
  CheckCircle2,
  Lock,
  PlayCircle,
  SkipForward,
  Circle,
  ChevronRight,
  TrendingUp,
} from "lucide-react";
import type { PlannedPath, NodeStatus, PathStep } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_META: Record<
  NodeStatus,
  { label: string; icon: any; className: string; ringClass: string }
> = {
  completed: {
    label: "已掌握",
    icon: CheckCircle2,
    className: "text-green-700 dark:text-fg",
    ringClass: "border-border",
  },
  skipped: {
    label: "已跳过",
    icon: SkipForward,
    className: "text-fg-subtle",
    ringClass: "border-border opacity-60",
  },
  in_progress: {
    label: "进行中",
    icon: PlayCircle,
    className: "text-brand-700 dark:text-fg",
    ringClass: "border-border",
  },
  available: {
    label: "可学习",
    icon: Circle,
    className: "text-brand-600 dark:text-fg-muted",
    ringClass: "border-border",
  },
  locked: {
    label: "未解锁",
    icon: Lock,
    className: "text-fg-subtle",
    ringClass: "border-border",
  },
};

export function PathVisualizer({
  path,
  loading = false,
  error = null,
  stale = false,
}: {
  path: PlannedPath | null;
  loading?: boolean;
  error?: string | null;
  stale?: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  if (loading) {
    return <div className="p-6 text-center text-xs text-fg-muted">学习路径加载中…</div>;
  }
  if (error) {
    return (
      <div className="p-6 text-center text-xs text-red-700 dark:text-fg">
        <p>学习路径加载失败</p>
        <p className="mt-1 text-fg-muted">{error}</p>
      </div>
    );
  }
  if (!path || path.nodes.length === 0) {
    return <div className="p-6 text-center text-xs text-fg-muted">暂无学习路径</div>;
  }

  const completed = path.completed_count;
  const total = path.nodes.length;
  const pct = total > 0 ? (completed / total) * 100 : 0;

  const toggle = (id: string) => {
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="p-4">
      {stale && (
        <div className="mb-3 rounded-md border border-dashed border-border bg-bg-subtle p-3 text-xs text-fg-muted">
          学习状态有更新，建议重新规划接下来的内容
        </div>
      )}
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp className="w-4 h-4 text-brand-400" />
        <h3 className="font-semibold text-sm">学习路径</h3>
        <span className="text-[10px] text-fg-muted ml-auto">
          {completed}/{total} · {pct.toFixed(0)}% · {path.total_estimated_hours}h
        </span>
      </div>

      <div className="h-1.5 bg-bg-panel rounded-full overflow-hidden mb-3">
        <div
          className="h-full bg-brand-500 dark:bg-fg-muted transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="border-t border-border">
        {path.nodes.map((step, i) => {
          const meta = STATUS_META[step.status];
          const Icon = meta.icon;
          const stepId = step.node_id || step.id || String(i);
          const isExpanded = expanded.has(stepId);
          return (
            <PathNodeRow
              key={stepId}
              step={step}
              index={i + 1}
              meta={meta}
              Icon={Icon}
              expanded={isExpanded}
              onToggle={() => toggle(stepId)}
            />
          );
        })}
      </div>
    </div>
  );
}

function PathNodeRow({
  step,
  index,
  meta,
  Icon,
  expanded,
  onToggle,
}: {
  step: PathStep;
  index: number;
  meta: (typeof STATUS_META)[NodeStatus];
  Icon: any;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className={cn("border-b", meta.ringClass)}>
      <button
        onClick={onToggle}
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
      >
        <span className="text-[10px] text-fg-muted shrink-0 w-4">
          {index}
        </span>
        <Icon className={cn("w-3.5 h-3.5 shrink-0", meta.className)} />
        <span className="text-xs flex-1 truncate">{step.name}</span>
        <span className="text-[10px] text-fg-muted shrink-0">
          {step.estimated_hours}h · 难度 {step.difficulty}
        </span>
        <span
          className={cn(
            "text-[9px] shrink-0",
            meta.className,
          )}
        >
          {meta.label}
        </span>
        <ChevronRight
          className={cn(
            "w-3 h-3 text-fg-muted shrink-0 transition-transform",
            expanded && "rotate-90",
          )}
        />
      </button>
      {expanded && (
        <div className="px-9 pb-3 text-[10px] text-fg-muted space-y-1 border-t border-border pt-2">
          {(step.prerequisites?.length ?? 0) > 0 && (
            <div>
              前置:{" "}
              {step.prerequisites?.map((p) => (
                <code key={p} className="text-accent mx-0.5 bg-bg/40 px-1 rounded">
                  {p}
                </code>
              ))}
            </div>
          )}
          <div className="flex items-center gap-3">
            <span>分类: {step.category || "—"}</span>
            <span>·</span>
            <span>预计 {step.estimated_hours}h</span>
          </div>
        </div>
      )}
    </div>
  );
}
