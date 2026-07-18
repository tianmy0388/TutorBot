"use client";

/**
 * ResourcePlanCard — plan-confirmation UI (Task 4 / Task 10).
 *
 * The user gets a recommended list of resource types. They can deselect
 * anything (especially video/PPT, which are expensive). The estimated
 * time updates as they toggle types. The confirm button sends the
 * selection to POST /api/v1/plans/{id}/confirm.
 */

import { useMemo, useState } from "react";
import { Check, Clock, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ResourcePlan } from "@/lib/types";

const ALL_TYPES: Array<{
  id: string;
  label: string;
  expensive?: boolean;
}> = [
  { id: "document", label: "讲解文档" },
  { id: "mindmap", label: "思维导图" },
  { id: "exercise", label: "练习题" },
  { id: "reading", label: "拓展阅读" },
  { id: "code", label: "代码示例" },
  { id: "video", label: "视频/动画", expensive: true },
  { id: "ppt", label: "PPT 课件", expensive: true },
];

const TYPE_ESTIMATED_SECONDS: Record<string, number> = {
  document: 15,
  mindmap: 10,
  exercise: 20,
  reading: 8,
  video: 90,
  code: 15,
  ppt: 60,
};

export interface ResourcePlanCardProps {
  plan: ResourcePlan;
  onConfirm: (selectedTypes: string[]) => void | Promise<void>;
  onCancel?: () => void;
}

export function ResourcePlanCard({
  plan,
  onConfirm,
  onCancel,
}: ResourcePlanCardProps) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(plan.recommended),
  );

  const estimatedSeconds = useMemo(
    () =>
      Array.from(selected).reduce(
        (acc, t) => acc + (TYPE_ESTIMATED_SECONDS[t] ?? 10),
        0,
      ),
    [selected],
  );

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const fmt = (s: number) => {
    if (s < 60) return `${s} 秒`;
    const m = Math.round(s / 60);
    return `~${m} 分钟`;
  };

  return (
    <section
      className="border-y border-brand-200 dark:border-border px-1 py-4 my-3 space-y-3"
      data-testid="resource-plan-card"
    >
      <header className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">资源生成计划</h3>
          <p className="text-xs text-fg-muted mt-0.5">
            主题: <span className="font-mono">{plan.topic}</span>
            {plan.rationale && <span className="ml-2">· {plan.rationale}</span>}
          </p>
        </div>
        <span
          className="text-xs text-fg-muted inline-flex items-center gap-1"
          data-testid="resource-plan-eta"
        >
          <Clock className="w-3.5 h-3.5" />
          预计 {fmt(estimatedSeconds)}
        </span>
      </header>

      <div className="flex flex-wrap gap-1.5">
        {ALL_TYPES.map((t) => {
          const isSelected = selected.has(t.id);
          const inRecommended = plan.recommended.includes(t.id);
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => toggle(t.id)}
              data-testid={`plan-toggle-${t.id}`}
              data-selected={isSelected}
              data-recommended={inRecommended}
              className={cn(
                "inline-flex items-center gap-1 px-2.5 h-7 rounded border text-xs transition-colors",
                isSelected
                  ? t.expensive
                    ? "border-yellow-300 bg-yellow-50 text-yellow-800 dark:border-border dark:bg-bg-subtle dark:text-fg"
                    : "border-brand-300 bg-brand-50 text-brand-700 dark:border-border dark:bg-bg-subtle dark:text-fg"
                  : "border-fg/10 text-fg-muted hover:border-fg/20",
              )}
            >
              {isSelected ? (
                <Check className="w-3 h-3" />
              ) : (
                <span className="w-3 h-3" />
              )}
              {t.label}
              {t.expensive && (
                <span className="text-[10px] text-yellow-700 dark:text-fg-muted ml-1">
                  耗时
                </span>
              )}
            </button>
          );
        })}
      </div>

      {plan.optional.length > 0 && (
        <p className="text-[11px] text-fg-subtle">
          可选：{plan.optional.join("、")}
        </p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        {onCancel && (
          <button
            className="btn-secondary text-sm h-8"
            onClick={onCancel}
            data-testid="plan-cancel"
          >
            <X className="w-3.5 h-3.5" /> 取消
          </button>
        )}
        <button
          className="btn-primary text-sm h-8"
          onClick={() => onConfirm(Array.from(selected))}
          disabled={selected.size === 0}
          data-testid="plan-confirm"
        >
          确认生成 ({selected.size} 项)
        </button>
      </div>
    </section>
  );
}
