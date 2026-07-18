"use client";

/**
 * ProfilePanel — learner progress and preference summary.
 *
 * Tabs:
 *  - Overview   : cognitive_style, modality (radar), pace, motivation
 *  - Knowledge  : knowledge map (mastery distribution + per-concept bars)
 *  - Errors     : error patterns list with frequencies
 *
 * Falls back to "anonymous" placeholder if profile hasn't been loaded.
 */

import { useState, useMemo } from "react";
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from "recharts";
import {
  Brain,
  Eye,
  Clock,
  Zap,
  Target,
  BookOpen,
  RefreshCw,
  TrendingUp,
  AlertCircle,
} from "lucide-react";
import { useProfile } from "@/hooks/useProfile";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";

type Tab = "overview" | "knowledge" | "errors";

// ---------------------------------------------------------------------------
// Top-level
// ---------------------------------------------------------------------------

export function ProfilePanel() {
  const { profile, loading, error, refresh } = useProfile();
  const [tab, setTab] = useState<Tab>("overview");
  const latestAssessment = useTutorStore((s) => s.latestAssessment);

  return (
    <div className="p-4 h-full flex flex-col overflow-hidden">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <h2 className="font-semibold flex items-center gap-2">
          <Brain className="w-4 h-4 text-brand-400" />
          学习状态
        </h2>
        <button
          onClick={refresh}
          disabled={loading}
          className="text-fg-muted hover:text-fg transition-colors p-1"
          title="刷新"
        >
          <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
        </button>
      </div>

      {!profile ? (
        <EmptyProfile loading={loading} error={error} onRefresh={refresh} />
      ) : (
        <>
          <div className="flex gap-4 mb-3 text-xs shrink-0 border-b border-border">
            {(["overview", "knowledge", "errors"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "px-0 pb-2 border-b-2 -mb-px transition-colors",
                  tab === t
                    ? "border-brand-500 text-brand-700 dark:border-fg-muted dark:text-fg"
                    : "border-transparent text-fg-muted hover:text-fg",
                )}
              >
                {t === "overview" ? "概览" : t === "knowledge" ? "知识" : "错误"}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto space-y-3 pr-1">
            {tab === "overview" && <OverviewTab profile={profile} />}
            {tab === "knowledge" && <KnowledgeTab profile={profile} />}
            {tab === "errors" && <ErrorsTab profile={profile} />}
            {latestAssessment && tab === "overview" && (
              <AssessmentSummary />
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty / loading / error
// ---------------------------------------------------------------------------

function EmptyProfile({
  loading,
  error,
  onRefresh,
}: {
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center text-fg-muted text-xs space-y-2 px-2">
      <Brain className="w-8 h-8 opacity-30" />
      <p>暂无学习状态</p>
      <p className="text-fg-subtle leading-relaxed">
        完成一次学习任务后，这里会逐步整理你的学习状态
      </p>
      {error && <p className="text-red-400">{error}</p>}
      <button
        onClick={onRefresh}
        disabled={loading}
        className="btn-ghost text-xs"
      >
        {loading ? "加载中…" : "重试"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview tab — radar chart + dimensions
// ---------------------------------------------------------------------------

function OverviewTab({
  profile,
}: {
  profile: NonNullable<ReturnType<typeof useProfile>["profile"]>;
}) {
  const cognitive = profile.cognitive_style;
  const modality = profile.modality;
  const pace = profile.pace;
  const motivation = profile.motivation;

  // Build radar chart data (modality preferences)
  const radarData = useMemo(() => {
    if (!modality) return [];
    const labels: Record<string, string> = {
      text: "阅读",
      video: "视频",
      interactive: "互动",
      diagram: "图解",
      code: "代码",
      audio: "音频",
      exercise: "练习",
    };
    return Object.entries(modality).map(([k, v]) => ({
      subject: labels[k] || k,
      value: Math.round((v as number) * 100),
      fullMark: 100,
    }));
  }, [modality]);

  const dominant = radarData.length
    ? radarData.reduce((a, b) => (b.value > a.value ? b : a))
    : null;

  return (
    <div className="space-y-3">
      {/* Modality radar chart */}
      {radarData.length > 0 && (
        <div className="py-3 border-t border-border">
          <div className="flex items-center gap-2 mb-2">
            <Target className="w-3.5 h-3.5 text-brand-600 dark:text-fg-muted" />
            <span className="text-xs font-medium">模态偏好雷达</span>
            {dominant && (
              <span className="ml-auto text-[10px] text-brand-600 dark:text-fg-muted">
                主导: {dominant.subject} ({dominant.value}%)
              </span>
            )}
          </div>
          <div className="h-44 -mx-1">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="70%">
                <PolarGrid stroke="rgb(var(--color-border))" />
                <PolarAngleAxis
                  dataKey="subject"
                  tick={{ fill: "rgb(var(--color-fg-muted))", fontSize: 10 }}
                />
                <PolarRadiusAxis
                  angle={90}
                  domain={[0, 100]}
                  tick={{ fill: "rgb(var(--color-fg-subtle))", fontSize: 9 }}
                  stroke="rgb(var(--color-border))"
                />
                <Radar
                  name="偏好"
                  dataKey="value"
                  stroke="rgb(var(--color-accent))"
                  fill="rgb(var(--color-accent))"
                  fillOpacity={0.2}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <DimensionCard
        icon={Eye}
        name="认知风格"
        value={cognitive}
        detail={cognitive ? `偏好 ${cognitive} 学习方式` : ""}
      />
      <DimensionCard
        icon={Clock}
        name="学习节奏"
        value={`${pace?.avg_session_duration_min ?? "?"} 分钟/次`}
        detail={`块大小 ${pace?.preferred_chunk_size_min ?? "?"} 分 · ${pace?.sessions_per_week ?? "?"} 次/周`}
      />
      <DimensionCard
        icon={Zap}
        name="动机与目标"
        value={motivation?.goal_type || ""}
        detail={
          motivation
            ? `紧迫度 ${motivation.urgency} · 自我效能 ${(motivation.self_efficacy * 100).toFixed(0)}%`
            : ""
        }
      />
      <DimensionCard
        icon={Target}
        name="目标详情"
        value={motivation?.goal_description || ""}
        detail={
          motivation?.target_completion_date
            ? `目标完成: ${motivation.target_completion_date}`
            : ""
        }
      />

      <div className="grid grid-cols-2 gap-2 mt-2">
        <StatBox
          label="已掌握"
          value={profile.strong_concepts.length}
          accent="green"
        />
        <StatBox
          label="待加强"
          value={profile.weak_concepts.length}
          accent="orange"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Knowledge tab — distribution + per-concept bars
// ---------------------------------------------------------------------------

function KnowledgeTab({
  profile,
}: {
  profile: NonNullable<ReturnType<typeof useProfile>["profile"]>;
}) {
  const entries = Object.entries(profile.knowledge_map || {}).sort(
    (a, b) => (b[1] as number) - (a[1] as number),
  );

  // Build distribution: histogram of mastery buckets
  const distribution = useMemo(() => {
    const buckets = [
      { range: "0-20%", count: 0, fill: "rgb(var(--color-chart-1))" },
      { range: "20-40%", count: 0, fill: "rgb(var(--color-chart-2))" },
      { range: "40-60%", count: 0, fill: "rgb(var(--color-chart-3))" },
      { range: "60-80%", count: 0, fill: "rgb(var(--color-chart-4))" },
      { range: "80-100%", count: 0, fill: "rgb(var(--color-chart-5))" },
    ];
    entries.forEach(([, v]) => {
      const pct = (v as number) * 100;
      const idx = Math.min(4, Math.floor(pct / 20));
      buckets[idx].count += 1;
    });
    return buckets;
  }, [entries]);

  if (entries.length === 0) {
    return (
      <div className="text-xs text-fg-muted text-center py-6">
        尚未记录任何知识点
      </div>
    );
  }

  const avgMastery =
    entries.reduce((s, [, v]) => s + (v as number), 0) / entries.length;

  return (
    <div className="space-y-3">
      {/* Summary header */}
      <div className="py-3 border-t border-border">
        <div className="flex items-center justify-between text-xs">
          <span className="text-fg-muted flex items-center gap-1">
            <BookOpen className="w-3 h-3" />
            知识点总数
          </span>
          <span className="text-fg font-semibold">{entries.length}</span>
        </div>
        <div className="flex items-center justify-between text-xs mt-1">
          <span className="text-fg-muted">平均掌握度</span>
          <span className="text-fg font-semibold">
            {(avgMastery * 100).toFixed(0)}%
          </span>
        </div>
        {/* Distribution chart */}
        <div className="h-24 mt-2 -mx-2">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={distribution}>
              <CartesianGrid
                stroke="rgb(var(--color-border))"
                strokeDasharray="3 3"
                vertical={false}
              />
              <XAxis
                dataKey="range"
                tick={{ fill: "rgb(var(--color-fg-muted))", fontSize: 9 }}
                stroke="rgb(var(--color-border))"
              />
              <YAxis
                tick={{ fill: "rgb(var(--color-fg-subtle))", fontSize: 9 }}
                stroke="rgb(var(--color-border))"
                width={20}
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={{
                  background: "rgb(var(--color-bg-panel))",
                  border: "1px solid rgb(var(--color-border))",
                  borderRadius: 4,
                  fontSize: 11,
                }}
                labelStyle={{ color: "rgb(var(--color-fg))" }}
                cursor={{ fill: "rgb(var(--color-bg-subtle))" }}
              />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {distribution.map((b, i) => (
                  <Cell key={i} fill={b.fill} fillOpacity={0.8} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Per-concept bars */}
      <div className="space-y-1.5">
        <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold px-1">
          掌握度详情
        </div>
        {entries.map(([concept, mastery]) => (
          <KnowledgeBar
            key={concept}
            concept={concept}
            mastery={mastery as number}
          />
        ))}
      </div>
    </div>
  );
}

function KnowledgeBar({ concept, mastery }: { concept: string; mastery: number }) {
  const pct = Math.round(mastery * 100);
  const color =
    pct >= 80
      ? "bg-green-600 dark:bg-fg-muted"
      : pct >= 50
      ? "bg-brand-500 dark:bg-fg-muted"
      : pct >= 30
      ? "bg-yellow-600 dark:bg-fg-muted"
      : "bg-red-600 dark:bg-fg-muted";
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-fg truncate flex-1">{concept}</span>
        <span className="text-fg-muted ml-2 shrink-0">{pct}%</span>
      </div>
      <div className="h-1.5 bg-bg-panel rounded-full overflow-hidden mt-0.5">
        <div
          className={cn("h-full transition-all", color)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Errors tab
// ---------------------------------------------------------------------------

function ErrorsTab({
  profile,
}: {
  profile: NonNullable<ReturnType<typeof useProfile>["profile"]>;
}) {
  const errors = profile.error_patterns || [];
  if (errors.length === 0) {
    return (
      <div className="text-xs text-fg-muted text-center py-6">
        <AlertCircle className="w-6 h-6 mx-auto mb-2 opacity-40" />
        暂无错误模式记录
      </div>
    );
  }
  // Sort by frequency desc
  const sorted = [...errors].sort((a, b) => b.frequency - a.frequency);
  return (
    <div className="space-y-2">
      {sorted.map((e, i) => (
        <div
          key={i}
          className="py-2.5 border-t border-border transition-colors"
        >
          <div className="flex items-center justify-between text-xs">
            <span className="text-fg font-medium truncate">{e.concept}</span>
            <span className="text-orange-700 dark:text-fg-muted text-[10px] shrink-0">
              ×{e.frequency}
            </span>
          </div>
          <div className="text-[11px] text-fg-muted mt-0.5">
            类型: {e.mistake_type}
          </div>
          {e.last_observed && (
            <div className="text-[10px] text-fg-subtle mt-0.5">
              最近: {new Date(e.last_observed).toLocaleDateString("zh-CN")}
            </div>
          )}
          {e.notes && (
            <div className="text-[11px] text-fg-muted mt-1 italic">
              {e.notes}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Assessment summary (latest)
// ---------------------------------------------------------------------------

function AssessmentSummary() {
  const a = useTutorStore((s) => s.latestAssessment)!;
  const trend = a.trajectory;
  const trendColor =
    trend === "improving"
      ? "text-green-400"
      : trend === "declining"
      ? "text-red-400"
      : trend === "stagnant"
      ? "text-yellow-400"
      : "text-fg-subtle";

  return (
    <div className="py-3 border-t border-border">
      <div className="flex items-center gap-2 mb-2">
        <TrendingUp className="w-3.5 h-3.5 text-brand-400" />
        <span className="text-xs font-medium">最近评估</span>
        <span className={cn("text-[10px] ml-auto", trendColor)}>
          {trend}
        </span>
      </div>
      <div className="flex items-baseline justify-between">
        <span className="text-2xl font-bold">
          {(a.overall_score * 100).toFixed(0)}
        </span>
        <span className="text-xs text-fg-muted">综合分</span>
      </div>
      {a.recommendations.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[10px] text-fg-muted">
          {a.recommendations.slice(0, 2).map((r, i) => (
            <li key={i}>· {r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small bits
// ---------------------------------------------------------------------------

function DimensionCard({
  icon: Icon,
  name,
  value,
  detail,
}: {
  icon: any;
  name: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="py-3 border-t border-border">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="w-4 h-4 text-brand-400 shrink-0" />
        <span className="text-sm font-medium">{name}</span>
      </div>
      <div className="text-xs text-fg truncate">{value || "—"}</div>
      {detail && (
        <div className="text-[11px] text-fg-muted mt-0.5 truncate">
          {detail}
        </div>
      )}
    </div>
  );
}

function StatBox({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent: "green" | "orange";
}) {
  return (
    <div
      className={cn(
        "py-2.5 border-t border-border text-center",
        accent === "green"
          ? "text-green-800 dark:text-fg"
          : "text-orange-800 dark:text-fg",
      )}
    >
      <div className="text-2xl font-bold text-fg">{value}</div>
      <div className="text-[11px] text-fg-muted mt-0.5">{label}</div>
    </div>
  );
}
