"use client";

/**
 * JobProgressCard — per-job progress + cancel + retry-failed UI.
 */

import { useState } from "react";
import { CheckCircle2, Loader2, RefreshCw, XCircle, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ClientJob } from "@/lib/job-reducer";

export interface JobProgressCardProps {
  job: ClientJob;
  onCancel?: () => void;
  onRetryFailed?: () => void;
}

export function JobProgressCard({
  job,
  onCancel,
  onRetryFailed,
}: JobProgressCardProps) {
  const isTerminal =
    job.status === "succeeded" ||
    job.status === "partial" ||
    job.status === "failed" ||
    job.status === "cancelled";
  const isFailed = job.status === "failed" || job.status === "partial";

  const succeeded = job.result?.artifacts?.filter((a) => a.status === "succeeded") ?? [];
  const failed =
    job.result?.artifacts?.filter((a) => a.status === "failed") ?? [];

  return (
    <section
      className={cn(
        "rounded-xl border p-3 my-2 text-sm",
        job.status === "succeeded" && "border-green-500/30 bg-green-500/5",
        job.status === "partial" && "border-yellow-500/30 bg-yellow-500/5",
        job.status === "failed" && "border-red-500/30 bg-red-500/5",
        job.status === "cancelled" && "border-fg/10 bg-bg-card",
        !isTerminal && "border-blue-500/30 bg-blue-500/5",
      )}
      data-testid="job-progress-card"
      data-job-status={job.status}
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {!isTerminal && (
            <Loader2 className="w-4 h-4 animate-spin text-blue-300 shrink-0" />
          )}
          {job.status === "succeeded" && (
            <CheckCircle2 className="w-4 h-4 text-green-300 shrink-0" />
          )}
          {job.status === "partial" && (
            <AlertCircle className="w-4 h-4 text-yellow-300 shrink-0" />
          )}
          {job.status === "failed" && (
            <XCircle className="w-4 h-4 text-red-300 shrink-0" />
          )}
          {job.status === "cancelled" && (
            <XCircle className="w-4 h-4 text-fg-muted shrink-0" />
          )}
          <span className="font-medium truncate">{capabilityLabel(job.capability)}</span>
          <span className="text-fg-muted text-xs">
            {job.status === "pending" && "排队中"}
            {job.status === "running" && "执行中"}
            {job.status === "succeeded" && "完成"}
            {job.status === "partial" && "部分完成"}
            {job.status === "failed" && "失败"}
            {job.status === "cancelled" && "已取消"}
          </span>
        </div>
        {onCancel && !isTerminal && (
          <button
            className="btn-secondary text-xs h-7"
            onClick={onCancel}
            data-testid="job-cancel"
          >
            取消
          </button>
        )}
        {isFailed && onRetryFailed && failed.length > 0 && (
          <button
            className="btn-secondary text-xs h-7"
            onClick={onRetryFailed}
            data-testid="job-retry-failed"
          >
            <RefreshCw className="w-3 h-3 mr-1" /> 重试失败项
          </button>
        )}
      </header>

      {(succeeded.length > 0 || failed.length > 0) && (
        <ul className="mt-2 space-y-0.5 text-xs" data-testid="job-artifacts">
          {succeeded.map((a) => (
            <li
              key={a.resource_type}
              className="text-green-300 inline-flex items-center gap-1 mr-3"
            >
              <CheckCircle2 className="w-3 h-3" /> {a.resource_type}
            </li>
          ))}
          {failed.map((a) => (
            <li
              key={a.resource_type}
              className="text-red-300 inline-flex items-center gap-1 mr-3"
            >
              <XCircle className="w-3 h-3" /> {a.resource_type}
              {a.error?.code && (
                <span className="text-fg-subtle">[{a.error.code}]</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function capabilityLabel(cap: string): string {
  switch (cap) {
    case "tutoring":
      return "即时答疑";
    case "resource_generation":
      return "资源生成";
    case "assessment":
      return "效果评估";
    case "path_planning":
      return "路径规划";
    case "profile":
      return "学习画像";
    default:
      return cap;
  }
}
