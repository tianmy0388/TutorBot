"use client";

/**
 * TaskProcessCard — the generation-progress / workflow-timeline card.
 *
 * One component, two data sources (2026-07-19 plan):
 *   - live: built from the reducer's ClientJob via ``taskProcessFromJob``
 *     (stage chips + last-8 progress messages + resource count);
 *   - persisted: built from a ``workflow_timeline`` chat message via
 *     ``taskProcessFromWorkflowMessage`` (all stages resolved, duration,
 *     collapsible progress detail).
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, LoaderCircle } from "lucide-react";

import type { TaskProcessData, TaskProcessStage } from "@/lib/task-process";
import { formatDuration } from "@/lib/task-process";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<string, string> = {
  succeeded: "已完成",
  partial: "已完成主要内容",
  failed: "需要再试一次",
  cancelled: "已停止",
};

export function TaskProcessCard({ data }: { data: TaskProcessData }) {
  const live = data.status === "active";
  return (
    <section
      className="animate-fade-in border-b border-border py-7 first:pt-0"
      aria-live={live ? "polite" : undefined}
    >
      <div className="flex items-center justify-between gap-4 text-[11px] font-semibold text-fg-muted">
        <span className="flex items-center gap-2">
          {live && (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
          )}
          这次学习
        </span>
        <span>{live ? "正在进行" : STATUS_LABEL[data.status]}</span>
      </div>

      {data.stages.length > 0 && (
        <div
          className="mt-3 flex flex-wrap gap-2"
          data-testid="task-process-stages"
        >
          {data.stages.map((stage) => (
            <StageChip key={stage.label} stage={stage} />
          ))}
        </div>
      )}

      {live && <LiveProgress progress={data.progress} />}

      {!live && data.progress.length > 0 && (
        <ProgressDetail progress={data.progress} />
      )}

      <div className="mt-3 flex items-center gap-3 text-[11px] text-fg-subtle">
        {data.resourceCount > 0 && <span>已产出 {data.resourceCount} 项资源</span>}
        {!live && data.durationMs != null && (
          <span>耗时 {formatDuration(data.durationMs)}</span>
        )}
      </div>

      {data.error && (
        <div className="mt-4 rounded-2xl border border-border bg-bg-subtle p-4 text-sm">
          <p className="flex items-center gap-2 font-semibold">
            <AlertTriangle className="h-4 w-4" />
            这一步暂时没有完成
          </p>
          <p className="mt-1 text-fg-muted">{data.error.message}</p>
          <details className="mt-3 text-xs text-fg-muted">
            <summary className="cursor-pointer font-medium text-fg">
              来源与说明
            </summary>
            <p className="mt-2 leading-5">错误编号：{data.error.code}</p>
          </details>
        </div>
      )}
    </section>
  );
}

function StageChip({ stage }: { stage: TaskProcessStage }) {
  return (
    <span
      className={cn(
        "inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border px-3 text-xs",
        stage.state === "active"
          ? "bg-bg-panel text-fg"
          : stage.state === "pending"
            ? "text-fg-subtle"
            : "bg-bg-subtle text-fg-muted",
      )}
    >
      {stage.state === "completed" && <Check className="h-3.5 w-3.5" />}
      {stage.state === "active" && (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
      )}
      {stage.state === "pending" && (
        <span className="h-1.5 w-1.5 rounded-full bg-fg-subtle" />
      )}
      {stage.state === "incomplete" && (
        <span className="h-1.5 w-1.5 rounded-full bg-fg-muted" />
      )}
      {stage.label}
    </span>
  );
}

function LiveProgress({ progress }: { progress: string[] }) {
  const scrollRef = useRef<HTMLUListElement | null>(null);
  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [progress.length]);

  if (progress.length === 0) {
    return (
      <p className="mt-3 text-sm text-fg-muted">
        请稍等一下，完成的内容会直接出现在这里。
      </p>
    );
  }
  return (
    <ul
      ref={scrollRef}
      className="mt-3 max-h-40 space-y-1 overflow-y-auto text-xs text-fg-muted"
      data-testid="task-process-live-progress"
    >
      {progress.map((text, index) => (
        <ProgressLine key={index} text={text} />
      ))}
    </ul>
  );
}

/** Collapsed-by-default progress detail for a finished task. Controlled
 * (not native <details> toggling) so the list only exists in the DOM
 * once the user expands it. */
function ProgressDetail({ progress }: { progress: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      open={open}
      className="mt-3 text-xs text-fg-muted"
      data-testid="task-process-progress-detail"
    >
      <summary
        className="cursor-pointer font-medium text-fg"
        onClick={(event) => {
          event.preventDefault();
          setOpen((value) => !value);
        }}
      >
        过程明细（{progress.length} 条）
      </summary>
      {open && (
        <ul className="mt-2 space-y-1">
          {progress.map((text, index) => (
            <ProgressLine key={index} text={text} />
          ))}
        </ul>
      )}
    </details>
  );
}

/** One progress line. The bullet marker lives in an aria-hidden span so
 * the line's own text is exactly the progress message. */
function ProgressLine({ text }: { text: string }) {
  return (
    <li className="leading-5">
      <span aria-hidden="true">· </span>
      {text}
    </li>
  );
}
