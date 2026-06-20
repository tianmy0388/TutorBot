"use client";

/**
 * AssessmentPanel — latest learning effectiveness assessment.
 *
 * Layout:
 *  1. Overall score + trajectory badge + stats (events analyzed, window)
 *  2. Six dimension scores (knowledge_mastery, engagement, comprehension,
 *     pace, gaps, trajectory) — each as a colored bar
 *  3. Weak concepts + strong concepts
 *  4. Adaptive strategy actions (recommend_review / advance / practice / …)
 *  5. Recommendations (free-text list)
 *
 * Falls back to friendly empty state if no assessment has run yet.
 */

import { useState } from "react";
import {
  BarChart3,
  TrendingUp,
  TrendingDown,
  Minus,
  Target,
  Zap,
  Brain,
  Clock,
  AlertTriangle,
  ListChecks,
  Activity,
  Sparkles,
  ChevronRight,
} from "lucide-react";
import { useTutorStore } from "@/lib/store";
import type {
  ActionType,
  AssessmentDimension,
  AssessmentReport,
  StrategyDecision,
  TrajectoryTrend,
} from "@/lib/types";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Dimension metadata
// ---------------------------------------------------------------------------

const DIM_META: Record<
  AssessmentDimension,
  { label: string; icon: any; color: string; bgClass: string }
> = {
  knowledge_mastery: {
    label: "知识掌握",
    icon: Brain,
    color: "text-blue-300",
    bgClass: "bg-blue-500",
  },
  engagement: {
    label: "参与度",
    icon: Zap,
    color: "text-yellow-300",
    bgClass: "bg-yellow-500",
  },
  comprehension: {
    label: "理解深度",
    icon: Target,
    color: "text-purple-300",
    bgClass: "bg-purple-500",
  },
  pace: {
    label: "学习节奏",
    icon: Clock,
    color: "text-cyan-300",
    bgClass: "bg-cyan-500",
  },
  gaps: {
    label: "薄弱点",
    icon: AlertTriangle,
    color: "text-orange-300",
    bgClass: "bg-orange-500",
  },
  trajectory: {
    label: "进步趋势",
    icon: TrendingUp,
    color: "text-green-300",
    bgClass: "bg-green-500",
  },
};

const TRAJECTORY_META: Record<
  TrajectoryTrend,
  { label: string; icon: any; color: string; ring: string }
> = {
  improving: {
    label: "上升中",
    icon: TrendingUp,
    color: "text-green-300",
    ring: "bg-green-950/30 border-green-700/40",
  },
  declining: {
    label: "下滑中",
    icon: TrendingDown,
    color: "text-red-300",
    ring: "bg-red-950/30 border-red-700/40",
  },
  stagnant: {
    label: "停滞",
    icon: Minus,
    color: "text-yellow-300",
    ring: "bg-yellow-950/30 border-yellow-700/40",
  },
  insufficient_data: {
    label: "数据不足",
    icon: Activity,
    color: "text-fg-muted",
    ring: "bg-bg-card border-fg/10",
  },
};

const ACTION_META: Record<
  ActionType,
  { label: string; icon: any; color: string; bgClass: string }
> = {
  recommend_review: {
    label: "复习",
    icon: ListChecks,
    color: "text-blue-300",
    bgClass: "bg-blue-950/30 border-blue-800/30",
  },
  recommend_advance: {
    label: "进阶",
    icon: TrendingUp,
    color: "text-green-300",
    bgClass: "bg-green-950/30 border-green-800/30",
  },
  recommend_practice: {
    label: "练习",
    icon: Sparkles,
    color: "text-purple-300",
    bgClass: "bg-purple-950/30 border-purple-800/30",
  },
  recommend_tutoring: {
    label: "答疑",
    icon: Brain,
    color: "text-pink-300",
    bgClass: "bg-pink-950/30 border-pink-800/30",
  },
  recommend_break: {
    label: "休息",
    icon: Clock,
    color: "text-yellow-300",
    bgClass: "bg-yellow-950/30 border-yellow-800/30",
  },
  adjust_pace: {
    label: "调速",
    icon: Activity,
    color: "text-cyan-300",
    bgClass: "bg-cyan-950/30 border-cyan-800/30",
  },
  no_action: {
    label: "无需调整",
    icon: Minus,
    color: "text-fg-muted",
    bgClass: "bg-bg-card border-fg/10",
  },
};

// ---------------------------------------------------------------------------
// Top-level
// ---------------------------------------------------------------------------

export function AssessmentPanel() {
  const report = useTutorStore((s) => s.latestAssessment);
  const strategy = useTutorStore((s) => s.latestStrategy);

  if (!report) {
    return <EmptyAssessment />;
  }

  return (
    <div className="p-5 border-b border-fg/10 h-full flex flex-col overflow-hidden">
      <div className="flex items-center gap-2 mb-4 shrink-0">
        <BarChart3 className="w-4 h-4 text-brand-400" />
        <h2 className="font-semibold">学习效果评估</h2>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        <OverallBlock report={report} />
        <DimensionsBlock report={report} />
        <ConceptsBlock report={report} />
        {strategy && <StrategyBlock strategy={strategy} />}
        {report.recommendations && report.recommendations.length > 0 && (
          <RecommendationsBlock items={report.recommendations} />
        )}
        {report.notes && <NotesBlock notes={report.notes} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyAssessment() {
  return (
    <div className="p-5 border-b border-fg/10 h-full flex flex-col items-center justify-center text-center text-fg-muted text-xs space-y-2 px-2">
      <BarChart3 className="w-8 h-8 opacity-30" />
      <p>暂无评估结果</p>
      <p className="text-fg-subtle leading-relaxed">
        在聊天中选择"效果评估"能力后点击生成
        <br />
        或系统会在积累足够事件后自动评估
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overall block
// ---------------------------------------------------------------------------

function OverallBlock({ report }: { report: AssessmentReport }) {
  const t = TRAJECTORY_META[report.trajectory] || TRAJECTORY_META.insufficient_data;
  const TIcon = t.icon;
  const score = Math.round((report.overall_score || 0) * 100);

  return (
    <div className={cn("p-3 rounded-lg border", t.ring)}>
      <div className="flex items-center gap-2 mb-2">
        <TIcon className={cn("w-4 h-4", t.color)} />
        <span className={cn("text-xs font-semibold", t.color)}>{t.label}</span>
        <span className="ml-auto text-[10px] text-fg-muted">
          {report.events_analyzed} 事件 · {report.event_window_hours}h 窗口
        </span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-bold text-fg">{score}</span>
        <span className="text-xs text-fg-muted">综合分</span>
      </div>
      <div className="mt-2 h-1.5 bg-bg-panel rounded-full overflow-hidden">
        <div
          className={cn(
            "h-full bg-gradient-to-r",
            score >= 70
              ? "from-green-500 to-emerald-400"
              : score >= 40
              ? "from-brand-500 to-brand-400"
              : "from-orange-500 to-red-400",
          )}
          style={{ width: `${score}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dimensions
// ---------------------------------------------------------------------------

function DimensionsBlock({ report }: { report: AssessmentReport }) {
  const dims = Object.entries(report.dimension_scores || {}) as Array<
    [AssessmentDimension, AssessmentReport["dimension_scores"][AssessmentDimension]]
  >;

  if (dims.length === 0) return null;

  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold mb-2 px-1">
        六维评分
      </div>
      <div className="space-y-1.5">
        {dims.map(([key, dim]) => (
          <DimensionBar key={key} dimKey={key} dim={dim} />
        ))}
      </div>
    </div>
  );
}

function DimensionBar({
  dimKey,
  dim,
}: {
  dimKey: AssessmentDimension;
  dim: AssessmentReport["dimension_scores"][AssessmentDimension];
}) {
  const [expanded, setExpanded] = useState(false);
  const meta = DIM_META[dimKey];
  const Icon = meta.icon;
  const pct = Math.round((dim?.score || 0) * 100);

  return (
    <div className="p-2.5 bg-bg-card rounded-lg border border-fg/5">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 text-left"
      >
        <Icon className={cn("w-3.5 h-3.5 shrink-0", meta.color)} />
        <span className="text-xs flex-1">{meta.label}</span>
        <span className="text-[10px] text-fg-muted tabular-nums shrink-0">
          {pct}
        </span>
        <div className="w-16 h-1.5 bg-bg-panel rounded-full overflow-hidden shrink-0">
          <div
            className={cn("h-full", meta.bgClass)}
            style={{ width: `${pct}%`, opacity: 0.85 }}
          />
        </div>
        <ChevronRight
          className={cn(
            "w-3 h-3 text-fg-muted shrink-0 transition-transform",
            expanded && "rotate-90",
          )}
        />
      </button>
      {expanded && (
        <div className="mt-2 pt-2 border-t border-fg/5 space-y-1 text-[10px]">
          {dim?.notes && (
            <div className="text-fg-muted leading-relaxed">{dim.notes}</div>
          )}
          {dim?.evidence && dim.evidence.length > 0 && (
            <ul className="text-fg-subtle space-y-0.5">
              {dim.evidence.slice(0, 4).map((ev, i) => (
                <li key={i}>· {ev}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Concepts
// ---------------------------------------------------------------------------

function ConceptsBlock({ report }: { report: AssessmentReport }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <ConceptList
        title="薄弱"
        items={report.weak_concepts || []}
        accent="orange"
      />
      <ConceptList
        title="掌握"
        items={report.strong_concepts || []}
        accent="green"
      />
    </div>
  );
}

function ConceptList({
  title,
  items,
  accent,
}: {
  title: string;
  items: string[];
  accent: "green" | "orange";
}) {
  if (!items || items.length === 0) return null;
  return (
    <div
      className={cn(
        "p-2.5 rounded-lg border",
        accent === "green"
          ? "bg-green-950/20 border-green-800/30"
          : "bg-orange-950/20 border-orange-800/30",
      )}
    >
      <div className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold mb-1.5">
        {title}概念
      </div>
      <div className="space-y-0.5">
        {items.slice(0, 6).map((c, i) => (
          <div key={i} className="text-[11px] text-fg truncate">
            · {c}
          </div>
        ))}
        {items.length > 6 && (
          <div className="text-[10px] text-fg-subtle">
            +{items.length - 6} 更多
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Strategy actions
// ---------------------------------------------------------------------------

function StrategyBlock({ strategy }: { strategy: StrategyDecision }) {
  const actions = strategy.actions || [];
  if (actions.length === 0) return null;

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <Sparkles className="w-3.5 h-3.5 text-accent" />
        <span className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold">
          自适应策略
        </span>
        <span className="ml-auto text-[10px] text-fg-subtle">
          {actions.length} 行动
        </span>
      </div>
      {strategy.overall_directive && (
        <div className="mb-2 p-2.5 bg-bg-card rounded-lg border border-fg/5 text-[11px] text-fg-muted italic">
          {strategy.overall_directive}
        </div>
      )}
      <div className="space-y-1.5">
        {actions.map((a, i) => (
          <ActionRow key={i} action={a} />
        ))}
      </div>
    </div>
  );
}

function ActionRow({
  action,
}: {
  action: StrategyDecision["actions"][number];
}) {
  const meta = ACTION_META[action.action_type] || ACTION_META.no_action;
  const Icon = meta.icon;
  return (
    <div className={cn("p-2.5 rounded-lg border", meta.bgClass)}>
      <div className="flex items-center gap-2 mb-1">
        <Icon className={cn("w-3.5 h-3.5 shrink-0", meta.color)} />
        <span className={cn("text-xs font-medium", meta.color)}>
          {meta.label}
        </span>
        <span className="ml-auto text-[10px] text-fg-subtle shrink-0">
          优先级 {action.priority?.toFixed?.(2) ?? action.priority ?? "—"}
        </span>
      </div>
      {action.target_concept && (
        <div className="text-[11px] text-fg truncate">
          目标:{" "}
          <code className="text-accent font-mono">{action.target_concept}</code>
        </div>
      )}
      {action.target_resource_type && (
        <div className="text-[10px] text-fg-muted">
          资源类型: {action.target_resource_type}
        </div>
      )}
      {action.rationale && (
        <div className="text-[10px] text-fg-subtle italic mt-1 leading-relaxed">
          {action.rationale}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recommendations / notes
// ---------------------------------------------------------------------------

function RecommendationsBlock({ items }: { items: string[] }) {
  return (
    <div className="p-3 bg-bg-card rounded-lg border border-fg/5">
      <div className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold mb-2">
        建议
      </div>
      <ul className="space-y-1">
        {items.map((r, i) => (
          <li key={i} className="text-[11px] text-fg leading-relaxed">
            · {r}
          </li>
        ))}
      </ul>
    </div>
  );
}

function NotesBlock({ notes }: { notes: string }) {
  return (
    <div className="p-3 bg-bg-card rounded-lg border border-fg/5">
      <div className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold mb-2">
        备注
      </div>
      <div className="text-[11px] text-fg-muted leading-relaxed">{notes}</div>
    </div>
  );
}