"use client";

/**
 * JobTray — Phase 5.2 async-job control panel.
 *
 * Shows the user's recent jobs in a popover attached to a header badge.
 * Active jobs (pending/running) are highlighted at the top; completed /
 * failed / cancelled jobs are below. Each row exposes:
 *
 *   - status icon + label
 *   - capability + message preview
 *   - duration / event count
 *   - action: subscribe (re-attach to live events), cancel, delete
 *
 * Pair with `useJobQueue` to drive submit / subscribe / cancel.
 */

import { useState } from "react";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  Ban,
  Clock,
  PlayCircle,
  X,
  Trash2,
  RefreshCw,
  Eye,
  History,
  Files,
  MessageCircle,
  BarChart3,
  Compass,
  Brain,
} from "lucide-react";
import { useJobQueue } from "@/hooks/useJobQueue";
import { useTutorStore } from "@/lib/store";
import type { JobStatus, JobSummary } from "@/lib/types";
import { isJobTerminal, isTerminal } from "@/lib/job-reducer";
import { formatStructuredError } from "@/lib/errors";
import { cn } from "@/lib/utils";

const STATUS_META: Record<
  JobStatus,
  { label: string; icon: any; ring: string; color: string }
> = {
  pending: {
    label: "排队中",
    icon: Clock,
    color: "text-yellow-700 dark:text-fg-muted",
    ring: "bg-yellow-50 border-yellow-200 dark:bg-bg-subtle dark:border-border",
  },
  running: {
    label: "运行中",
    icon: Loader2,
    color: "text-brand-700 dark:text-fg",
    ring: "bg-brand-50 border-brand-200 dark:bg-bg-subtle dark:border-border",
  },
  succeeded: {
    label: "已完成",
    icon: CheckCircle2,
    color: "text-green-700 dark:text-fg",
    ring: "bg-green-50 border-green-200 dark:bg-bg-subtle dark:border-border",
  },
  partial: {
    label: "部分完成",
    icon: CheckCircle2,
    color: "text-yellow-700 dark:text-fg-muted",
    ring: "bg-yellow-50 border-yellow-200 dark:bg-bg-subtle dark:border-border",
  },
  failed: {
    label: "失败",
    icon: XCircle,
    color: "text-red-700 dark:text-fg",
    ring: "bg-red-50 border-red-200 dark:bg-bg-subtle dark:border-border",
  },
  cancelled: {
    label: "已取消",
    icon: Ban,
    color: "text-fg-muted",
    ring: "bg-bg-card border-fg/10",
  },
};

const CAPABILITY_META: Record<string, { icon: any; color: string; label: string }> = {
  resource_generation: {
    icon: Files,
    color: "text-accent",
    label: "整理学习资料",
  },
  tutoring: { icon: MessageCircle, color: "text-brand-600 dark:text-fg-muted", label: "问题讲解" },
  assessment: { icon: BarChart3, color: "text-brand-600 dark:text-fg-muted", label: "检查掌握情况" },
  path_planning: { icon: Compass, color: "text-brand-600 dark:text-fg-muted", label: "安排下一步" },
  profile: { icon: Brain, color: "text-brand-600 dark:text-fg-muted", label: "学习状态" },
};

export function JobTray() {
  const userId = useTutorStore((s) => s.userId);
  const jobsById = useTutorStore((s) => s.jobsById);
  const queue = useJobQueue(userId);
  const [open, setOpen] = useState(false);

  const queueById = new Map(queue.jobs.map((job) => [job.job_id, job]));
  const mergedJobs: JobSummary[] = [
    ...Object.values(jobsById).map((durable): JobSummary => {
      const queued = queueById.get(durable.job_id);
      const startedAt = durable.started_at
        ? new Date(durable.started_at).toISOString()
        : (queued?.started_at ?? null);
      const finishedAt = durable.finished_at
        ? new Date(durable.finished_at).toISOString()
        : (queued?.finished_at ?? null);
      const durationSeconds =
        durable.started_at != null && durable.finished_at != null
          ? (durable.finished_at - durable.started_at) / 1000
          : (queued?.duration_seconds ?? null);

      return {
        ...queued,
        job_id: durable.job_id,
        user_id: queued?.user_id ?? userId ?? "anonymous",
        session_id: queued?.session_id ?? "",
        capability: durable.capability,
        status: durable.status,
        message_preview:
          durable.message_preview || queued?.message_preview || "",
        language: queued?.language ?? "zh",
        event_count: durable.event_count,
        created_at: new Date(durable.submitted_at).toISOString(),
        started_at: startedAt,
        finished_at: finishedAt,
        duration_seconds: durationSeconds,
        has_result: durable.result != null || (queued?.has_result ?? false),
        error: durable.error,
        children: durable.children ?? queued?.children,
        background_status:
          durable.background_status ?? queued?.background_status,
      };
    }),
    ...queue.jobs.filter((job) => !jobsById[job.job_id]),
  ].sort(
    (left, right) =>
      Date.parse(right.created_at) - Date.parse(left.created_at),
  );
  const recent = mergedJobs.slice(0, 8);
  const active = mergedJobs.filter((job) => {
    const durable = jobsById[job.job_id];
    return durable ? !isJobTerminal(durable) : !isTerminal(job.status);
  }).length;

  return (
    <div className="relative">
      {/* Trigger button (badge) */}
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex min-h-11 items-center gap-1.5 rounded-full border px-3 text-[11px] transition-colors",
          active > 0
            ? "bg-brand-100 border-brand-300 text-brand-700 dark:bg-bg-subtle dark:border-border dark:text-fg animate-pulse"
            : "bg-bg-panel border-fg/10 text-fg-muted hover:text-fg",
        )}
        title="任务记录"
      >
        {active > 0 ? (
          <Loader2 className="w-3 h-3 animate-spin" />
        ) : (
          <History className="w-3 h-3" />
        )}
        <span className="hidden sm:inline">任务</span>
        <span
          className={cn(
            "px-1 rounded text-[10px] font-mono",
            active > 0 ? "bg-brand-500 text-white" : "bg-bg-card text-fg-muted",
          )}
        >
          {active > 0 ? active : Math.max(queue.total, mergedJobs.length)}
        </span>
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div className="fixed left-3 right-3 top-[104px] z-50 max-h-[70vh] overflow-hidden rounded-md border border-border bg-bg-panel shadow-lg flex flex-col sm:absolute sm:left-auto sm:right-0 sm:top-full sm:mt-2 sm:w-[420px]">
            <header className="px-4 py-2.5 border-b border-fg/10 flex items-center gap-2 bg-bg-panel shrink-0">
              <History className="w-3.5 h-3.5 text-fg-muted" />
              <h3 className="font-semibold text-sm">任务记录</h3>
              {active > 0 && (
                <span className="px-1.5 py-0.5 rounded bg-brand-100 text-brand-700 dark:bg-bg-subtle dark:text-fg text-[10px] font-mono">
                  {active} 运行中
                </span>
              )}
              <button
                onClick={() => queue.refresh()}
                disabled={queue.loading}
                className="ml-auto text-fg-subtle hover:text-fg p-1"
                title="刷新"
              >
                <RefreshCw className={cn("w-3 h-3", queue.loading && "animate-spin")} />
              </button>
              <button
                onClick={() => setOpen(false)}
                className="text-fg-subtle hover:text-fg p-1"
              >
                <X className="w-3 h-3" />
              </button>
            </header>

            <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
              {queue.error && (
                <div className="p-2 rounded bg-red-50 dark:bg-bg-subtle border border-red-200 dark:border-border text-[11px] text-red-700 dark:text-fg">
                  {queue.error}
                </div>
              )}

              {recent.length === 0 && !queue.loading && (
                <div className="p-8 text-center text-xs text-fg-muted">
                  <History className="w-6 h-6 mx-auto mb-2 opacity-40" />
                  暂无任务
                </div>
              )}

              {recent.map((job) => (
                <JobRow
                  key={job.job_id}
                  job={job}
                  onSubscribe={() =>
                    queue.subscribe(job.job_id, job.capability, {
                      sessionId: job.session_id,
                    })
                  }
                  onCancel={() => queue.cancel(job.job_id)}
                  onRemove={() => queue.remove(job.job_id)}
                />
              ))}
            </div>

            {queue.stats && (
              <footer className="px-4 py-2 border-t border-fg/10 text-[10px] text-fg-muted shrink-0 bg-bg-panel/60 flex items-center gap-3">
                <span>总计 {queue.stats.job_count}</span>
                <span>·</span>
                <span>活跃 {queue.stats.active_count}</span>
                {Object.keys(queue.stats.by_status).length > 0 && (
                  <>
                    <span>·</span>
                    <span>
                      {Object.entries(queue.stats.by_status)
                        .map(([k, v]) => `${k}:${v}`)
                        .join(" / ")}
                    </span>
                  </>
                )}
              </footer>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Job row
// ---------------------------------------------------------------------------

function JobRow({
  job,
  onSubscribe,
  onCancel,
  onRemove,
}: {
  job: JobSummary;
  onSubscribe: () => void;
  onCancel: () => void;
  onRemove: () => void;
}) {
  const sm = STATUS_META[job.status] || STATUS_META.succeeded;
  const SIcon = sm.icon;
  const cm = CAPABILITY_META[job.capability] || {
    icon: PlayCircle,
    color: "text-fg-muted",
    label: job.capability,
  };
  const CIcon = cm.icon;

  const isActive = job.status === "pending" || job.status === "running";

  return (
    <div className={cn("rounded-lg border p-2.5", sm.ring)}>
      <div className="flex items-start gap-2">
        <CIcon className={cn("w-3.5 h-3.5 mt-0.5 shrink-0", cm.color)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-1">
            <SIcon
              className={cn(
                "w-3 h-3 shrink-0",
                sm.color,
                job.status === "running" && "animate-spin",
              )}
            />
            <span className={cn("text-[10px] font-semibold", sm.color)}>
              {sm.label}
            </span>
            <span className="text-[10px] text-fg-subtle">·</span>
            <span className="text-[10px] text-fg-muted">{cm.label}</span>
          </div>
          <div className="text-[11px] text-fg leading-relaxed line-clamp-2">
            {job.message_preview || "(无消息)"}
          </div>
          <div className="flex items-center gap-2 mt-1 text-[10px] text-fg-subtle">
            {job.duration_seconds != null && (
              <span>耗时 {job.duration_seconds.toFixed(1)}s</span>
            )}
            {job.started_at && !job.finished_at && (
              <span className="text-brand-700 dark:text-fg">运行中…</span>
            )}
            {job.created_at && (
              <span>
                {new Date(job.created_at).toLocaleTimeString("zh-CN", {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </span>
            )}
          </div>
          {job.error && (
            <div className="text-[10px] text-red-300 mt-1 line-clamp-2">
              {formatStructuredError(job.error)}
            </div>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 mt-2 pt-2 border-t border-fg/5">
        {isActive ? (
          <>
            <button
              onClick={onSubscribe}
              className="text-[10px] px-1.5 py-0.5 rounded bg-brand-100 text-brand-700 hover:bg-brand-200 dark:bg-bg-subtle dark:text-fg dark:hover:bg-bg-card flex items-center gap-1"
            >
              <Eye className="w-2.5 h-2.5" />
              查看
            </button>
            <button
              onClick={onCancel}
              className="text-[10px] px-1.5 py-0.5 rounded bg-red-950/30 text-red-300 border border-red-800/40 hover:bg-red-900/40 flex items-center gap-1"
            >
              <Ban className="w-2.5 h-2.5" />
              取消
            </button>
          </>
        ) : (
          <button
            onClick={onRemove}
            className="text-[10px] px-1.5 py-0.5 rounded text-fg-subtle hover:text-red-400 flex items-center gap-1"
          >
            <Trash2 className="w-2.5 h-2.5" />
            删除
          </button>
        )}
      </div>
    </div>
  );
}
