"use client";

/**
 * VideoViewer — Manim-generated video resource.
 *
 * Features:
 *  - HTML5 video player when video_url is available
 *  - Pending / rendering / failed status badges with progress hint
 *  - Scene list (if scenes[] provided in format_specific)
 *  - Manim source code viewer (collapsible)
 *  - Scene class + duration metadata
 */

import { useEffect, useState } from "react";
import {
  Play,
  Code2,
  AlertCircle,
  Film,
  Clock,
  Tag,
  Download,
  Copy,
  Check,
  RefreshCw,
} from "lucide-react";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs";
import type { Resource, VideoResourceFormat } from "@/lib/types";
import {
  getJobDetail,
  getResourcePackageDetail,
  retryVideoRender,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";

const RETRY_POLL_INTERVAL_MS = 1_000;
const MAX_AUTOMATIC_SYNC_FAILURES = 3;

export function createRetryPollingDelay(milliseconds: number): {
  wait: Promise<void>;
  cancel: () => void;
} {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let settle = () => {};
  const wait = new Promise<void>((resolve) => {
    let settled = false;
    settle = () => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve();
    };
    timer = setTimeout(settle, milliseconds);
  });
  return { wait, cancel: settle };
}

export function VideoViewer({ resource }: { resource: Resource }) {
  const latestPackage = useTutorStore((state) => state.latestPackage);
  const canonicalResource =
    latestPackage?.resources.find(
      (candidate) => candidate.resource_id === resource.resource_id,
    ) ?? resource;
  const canonicalPackageId =
    typeof canonicalResource.metadata?.package_id === "string"
      ? canonicalResource.metadata.package_id
      : latestPackage?.package_id ?? "";
  const canonicalRenderJobId =
    typeof canonicalResource.format_specific?.render_job_id === "string"
      ? canonicalResource.format_specific.render_job_id
      : "";
  const canonicalRepairJobId =
    typeof canonicalResource.format_specific?.repair_job_id === "string"
      ? canonicalResource.format_specific.repair_job_id
      : "";
  const renderChild = useTutorStore((state) =>
    Object.values(state.jobsById)
      .flatMap((job) => job.children ?? [])
      .filter(
        (candidate) =>
          candidate.task_kind === "video_render" &&
          (candidate.metadata?.resource_id === canonicalResource.resource_id ||
            candidate.dedupe_key?.endsWith(`:${canonicalResource.resource_id}`)) &&
          (!canonicalRenderJobId || candidate.job_id === canonicalRenderJobId),
      )
      .at(-1),
  );
  const repairChild = useTutorStore((state) =>
    Object.values(state.jobsById)
      .flatMap((job) => job.children ?? [])
      .filter(
        (candidate) =>
          candidate.task_kind === "video_repair_render" &&
          (candidate.metadata?.resource_id === canonicalResource.resource_id ||
            candidate.dedupe_key?.includes(
              `:${canonicalResource.resource_id}:`,
            )) &&
          (!canonicalRepairJobId || candidate.job_id === canonicalRepairJobId),
      )
      .at(-1),
  );
  const userId = useTutorStore((state) => state.userId);
  const reconcileVideoRetry = useTutorStore(
    (state) => state.reconcileVideoRetry,
  );
  const rehydrateJobFromDetail = useTutorStore(
    (state) => state.rehydrateJobFromDetail,
  );
  const setLatestPackage = useTutorStore((state) => state.setLatestPackage);
  // **2026-07-08 fix (fdb26152 regression):** ``resource.format_specific``
  // can be undefined for partial resources (e.g. the placeholder
  // cards emitted via ``contract.partial_artifacts`` on a FAILED
  // contract). The previous code did a non-null cast and crashed at
  // ``formatSpec.video_url``. Default to an empty object so all the
  // optional-chain reads below stay safe.
  const formatSpec = (canonicalResource.format_specific ?? {}) as VideoResourceFormat;

  const [showCode, setShowCode] = useState(false);
  const [copied, setCopied] = useState(false);
  // **2026-07-09 fix (ada95ede trace):** when the URL 404s (e.g.
  // /static/manim/<scene>.mp4 isn't reachable because the FastAPI
  // static mount wasn't wired yet), the ``<video>`` element silently
  // renders a 0-second blank player. We surface that explicitly as
  // an error banner with a one-click retry of the original render.
  const [videoLoadFailed, setVideoLoadFailed] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");
  const [retryTracking, setRetryTracking] = useState<{
    jobId: string;
    parentJobId: string;
    packageId: string;
  } | null>(null);
  const [retrySyncPaused, setRetrySyncPaused] = useState(false);
  const [retrySyncRevision, setRetrySyncRevision] = useState(0);

  const isReady = !!formatSpec.video_url && !videoLoadFailed;
  // When ``formatSpec.video_url`` is set but the network failed we
  // flip the UI into the failure banner instead of the player shell.
  const effectiveRenderStatus = videoLoadFailed
    ? "failed"
    : formatSpec.render_status === "failed"
      ? "failed"
      : formatSpec.render_status === "ready"
        ? "ready"
        : renderChild?.status === "failed" || renderChild?.status === "cancelled"
          ? "failed"
          : renderChild?.status === "succeeded"
            ? "succeeded"
            : renderChild?.status === "pending" || renderChild?.status === "running"
              ? renderChild.status
              : formatSpec.render_status ?? "unknown";
  const isFailed = effectiveRenderStatus === "failed";
  const isSucceeded =
    (effectiveRenderStatus === "succeeded" || effectiveRenderStatus === "ready") &&
    !isReady;
  // **2026-07-08 fix:** without ``formatSpec`` being defined, a
  // missing ``render_status`` shouldn't masquerade as "rendering".
  // We also collapse the three "not ready" states into one banner
  // when ``format_specific`` was missing entirely (partial artifact).
  // **2026-07-09 fix (ada95ede):** also collapse the broken-URL
  // state (videoLoadFailed) into the same banner.
  const renderStatus = effectiveRenderStatus;
  const isPending =
    !isReady &&
    !isFailed &&
    !isSucceeded &&
    (renderStatus === "pending" ||
      renderStatus === "rendering" ||
      renderStatus === "running");
  const isUnavailable = !isReady && !isFailed && !isSucceeded && !isPending;
  const persistedRepairActive =
    formatSpec.repair_status === "pending" ||
    formatSpec.repair_status === "running" ||
    (formatSpec.repair_status === undefined &&
      (repairChild?.status === "pending" || repairChild?.status === "running"));
  const isRepairActive = retrying || !!retryTracking || persistedRepairActive;
  const boundedRepairHistory = (formatSpec.repair_history ?? []).slice(-10);
  const latestRepairFailure = [...boundedRepairHistory]
    .reverse()
    .find((attempt) => attempt.status === "failed");
  const isRepairFailed =
    formatSpec.repair_status === "failed" && !isRepairActive;

  const failure = formatSpec.render_failure;
  const failureSummary = videoLoadFailed
    ? "视频文件无法加载"
    : failure?.summary || "渲染流程未生成可播放视频。";
  const tracebackTail = Array.isArray(failure?.traceback_tail)
    ? failure.traceback_tail.join("\n")
    : failure?.traceback_tail || "";
  const logArtifact = formatSpec.artifacts?.find(
    (artifact) =>
      artifact.kind === "render_log" ||
      (!!failure?.log_artifact_key &&
        artifact.artifact_key === failure.log_artifact_key),
  );
  const packageId = canonicalPackageId;
  const logUrl = logArtifact?.name
    ? `/api/v1/resources/packages/${encodeURIComponent(userId)}/${encodeURIComponent(packageId)}`
      + `/resources/${encodeURIComponent(canonicalResource.resource_id)}/artifacts/${encodeURIComponent(logArtifact.name)}`
    : "";

  const trackingJobId =
    retryTracking?.jobId || (persistedRepairActive ? canonicalRepairJobId : "");
  const trackingPollJobId =
    retryTracking?.parentJobId || repairChild?.parent_job_id || trackingJobId;
  const trackingPackageId = retryTracking?.packageId || packageId;

  useEffect(() => {
    if (
      !trackingJobId ||
      !trackingPollJobId ||
      !trackingPackageId ||
      retrySyncPaused
    ) return;
    let cancelled = false;
    let activeDelay: ReturnType<typeof createRetryPollingDelay> | null = null;

    const wait = async (milliseconds: number) => {
      const delay = createRetryPollingDelay(milliseconds);
      activeDelay = delay;
      await delay.wait;
      if (activeDelay === delay) activeDelay = null;
    };

    const track = async () => {
      let terminalObserved = false;
      let consecutiveFailures = 0;
      while (!cancelled) {
        try {
          if (!terminalObserved) {
            const detail = await getJobDetail(
              userId,
              trackingPollJobId,
            );
            if (cancelled) return;
            rehydrateJobFromDetail(detail);
            consecutiveFailures = 0;
            const currentStatus = detail.job_id === trackingJobId
              ? detail.status
              : detail.children?.find(
                  (candidate) => candidate.job_id === trackingJobId,
                )?.status;
            terminalObserved = !!currentStatus &&
              ["succeeded", "partial", "failed", "cancelled"].includes(
                currentStatus,
              );
          }
          if (terminalObserved) {
            const persisted = await getResourcePackageDetail(
              userId,
              trackingPackageId,
            );
            if (!cancelled) {
              setLatestPackage(persisted);
              setRetryError("");
              setRetryTracking(null);
            }
            return;
          }
          consecutiveFailures = 0;
          setRetryError("");
          await wait(RETRY_POLL_INTERVAL_MS);
        } catch (error) {
          if (cancelled) return;
          consecutiveFailures += 1;
          setRetryError(
            error instanceof Error ? error.message : "重试状态刷新失败",
          );
          if (consecutiveFailures >= MAX_AUTOMATIC_SYNC_FAILURES) {
            setRetrySyncPaused(true);
            return;
          }
          await wait(RETRY_POLL_INTERVAL_MS * consecutiveFailures);
        }
      }
    };
    void track();
    return () => {
      cancelled = true;
      activeDelay?.cancel();
    };
  }, [
    rehydrateJobFromDetail,
    retrySyncPaused,
    retrySyncRevision,
    setLatestPackage,
    trackingJobId,
    trackingPackageId,
    trackingPollJobId,
    userId,
  ]);

  const retry = async () => {
    if (!packageId || retrying || isRepairActive) return;
    setRetrying(true);
    setRetryError("");
    setRetrySyncPaused(false);
    try {
      const snapshot = await retryVideoRender(
        userId,
        packageId,
        canonicalResource.resource_id,
      );
      reconcileVideoRetry(snapshot);
      setRetryTracking({
        jobId: snapshot.job_id,
        parentJobId: snapshot.parent_job_id,
        packageId: snapshot.package_id,
      });
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : "重试请求失败");
    } finally {
      setRetrying(false);
    }
  };

  const resumeRetrySync = () => {
    if (!trackingJobId) return;
    setRetryError("");
    setRetrySyncPaused(false);
    setRetrySyncRevision((revision) => revision + 1);
  };

  const copy = async () => {
    if (!formatSpec.manim_code) return;
    await navigator.clipboard.writeText(formatSpec.manim_code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const formatDuration = (s?: number) => {
    if (!s) return "—";
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  };

  return (
    <div className="space-y-4">
      {/* Concept header */}
      {formatSpec.concept && (
        <div className="p-3 bg-gradient-to-r from-pink-950/30 to-purple-950/20 border border-pink-800/30 rounded-lg">
          <div className="flex items-center gap-2 text-pink-300 text-xs font-semibold mb-1">
            <Film className="w-3.5 h-3.5" />
            视频概念
          </div>
          <p className="text-sm text-fg">{formatSpec.concept}</p>
        </div>
      )}

      {/* Video player */}
      {isReady && formatSpec.video_url && (
        <div className="rounded-lg overflow-hidden bg-black border border-fg/10 shadow-lg">
          <video
            controls
            className="w-full max-h-[60vh]"
            poster=""
            preload="metadata"
            onError={() => {
              // **2026-07-09 fix (ada95ede):** a 404 from
              // /static/manim/<scene>.mp4 used to leave a blank
              // player. Surface the failure and switch to the
              // failure banner.
              // eslint-disable-next-line no-console
              console.warn(
                `[VideoViewer] failed to load video at ${formatSpec.video_url}`,
              );
              setVideoLoadFailed(true);
            }}
          >
            <source src={formatSpec.video_url} type="video/mp4" />
            您的浏览器不支持 video 标签。
          </video>
        </div>
      )}

      {trackingJobId && retryError && (
        <div className="rounded-lg border border-amber-700/40 bg-amber-950/20 p-3 text-left">
          <div className="text-xs text-amber-200">
            状态同步失败：{retryError}
          </div>
          {retrySyncPaused ? (
            <button
              type="button"
              aria-label="继续同步视频状态"
              onClick={resumeRetrySync}
              className="mt-2 inline-flex items-center gap-1 rounded border border-amber-700/50 px-2 py-1 text-xs text-amber-100 hover:bg-amber-900/30"
            >
              <RefreshCw className="h-3 w-3" />
              继续同步
            </button>
          ) : (
            <div className="mt-1 text-[11px] text-amber-300/80">
              正在自动重试…
            </div>
          )}
        </div>
      )}

      {isPending && (
        <div className="p-8 rounded-lg border border-fg/10 bg-bg-card text-center">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-pink-950/40 mb-3">
            <Play className="w-8 h-8 text-pink-400 animate-pulse" />
          </div>
          <div className="text-sm text-fg font-medium">视频渲染中…</div>
          <div className="mt-1 text-xs text-fg-muted">
            {renderStatus === "rendering"
              ? "正在执行 Manim 渲染任务"
              : "任务已排队 — 通常 30 秒内完成"}
          </div>
          <div className="mt-3 h-1 bg-bg-panel rounded-full overflow-hidden max-w-xs mx-auto">
            <div className="h-full bg-gradient-to-r from-pink-500 to-pink-400 animate-pulse w-2/3" />
          </div>
        </div>
      )}

      {isFailed && (
        <div className="p-4 rounded-lg border border-red-800/40 bg-red-950/20 flex gap-3">
          <AlertCircle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
          <div className="flex-1">
            <div className="text-sm font-medium text-red-300">
              {videoLoadFailed ? "视频加载失败" : "渲染失败"}
            </div>
            <div className="mt-1 text-xs text-red-400/80">
              {videoLoadFailed
                ? `无法访问 ${formatSpec.video_url} — 后端静态文件路由可能未配置（ada95ede fix），或视频文件已被清理。`
                : failureSummary}
            </div>
            {tracebackTail && (
              <details className="mt-2 text-xs text-red-300/90">
                <summary className="cursor-pointer select-none">查看技术详情</summary>
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded border border-red-800/30 bg-red-950/40 p-2 font-mono text-[11px]">
                  {tracebackTail}
                </pre>
              </details>
            )}
            {logUrl && (
              <a
                href={logUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-block text-xs text-red-300 underline hover:text-red-200"
              >
                查看完整渲染日志
              </a>
            )}
            {packageId && (
              <div className="mt-3">
                <button
                  type="button"
                  aria-label="智能修复并重新渲染"
                  onClick={retry}
                  disabled={isRepairActive}
                  className="inline-flex items-center gap-1 rounded border border-red-700/50 px-2 py-1 text-xs text-red-200 hover:bg-red-900/30 disabled:opacity-60"
                >
                  <RefreshCw className={cn("h-3 w-3", isRepairActive && "animate-spin")} />
                  {retrying ? "正在提交…" : "智能修复并重新渲染"}
                </button>
                {retryError && !trackingJobId && (
                  <div className="mt-1 text-xs text-red-300">{retryError}</div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {isRepairActive && (
        <div className="rounded-lg border border-violet-700/40 bg-violet-950/20 p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-violet-200">
            <RefreshCw className="h-4 w-4 animate-spin" />
            正在生成修复代码并重新渲染…
          </div>
          <div className="mt-1 text-xs text-violet-300/80">
            原始渲染失败信息会保留，完成后将自动同步最新视频。
          </div>
        </div>
      )}

      {isRepairFailed && (
        <div className="rounded-lg border border-amber-700/40 bg-amber-950/20 p-4">
          <div className="text-sm font-medium text-amber-200">智能修复失败</div>
          <div className="mt-1 text-xs text-amber-300/80">
            {latestRepairFailure?.summary || "本次修复未生成可播放视频，可再次手动修复。"}
          </div>
        </div>
      )}

      {isUnavailable && (
        <div className="rounded-lg border border-amber-800/40 bg-amber-950/20 p-4 text-sm text-amber-200">
          视频状态暂不可用，请刷新任务状态。
        </div>
      )}

      {isSucceeded && (
        <div className="p-4 rounded-lg border border-green-800/40 bg-green-950/20">
          <div className="text-sm font-medium text-green-300">渲染完成</div>
          <div className="mt-1 text-xs text-green-400/80">
            后台视频任务已成功完成，资源详情正在同步。
          </div>
        </div>
      )}

      {/* Metadata row */}
      <div className="flex items-center gap-3 text-xs text-fg-muted flex-wrap">
        {formatSpec.scene_class && (
          <span className="flex items-center gap-1">
            <Tag className="w-3 h-3" />
            <code className="text-accent bg-bg-panel px-1.5 py-0.5 rounded">
              {formatSpec.scene_class}
            </code>
          </span>
        )}
        {formatSpec.duration_seconds && (
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatDuration(formatSpec.duration_seconds)}
          </span>
        )}
        {formatSpec.resolution && <span>{formatSpec.resolution}</span>}
        {formatSpec.fps && <span>{formatSpec.fps} fps</span>}
        <div className="ml-auto flex items-center gap-1">
          {formatSpec.video_url && (
            <a
              href={formatSpec.video_url}
              download
              className="btn-ghost text-xs px-2 py-1 flex items-center gap-1"
              title="下载视频"
            >
              <Download className="w-3 h-3" />
              下载
            </a>
          )}
          <button
            onClick={() => setShowCode((s) => !s)}
            className="btn-ghost text-xs px-2 py-1 flex items-center gap-1"
          >
            <Code2 className="w-3 h-3" />
            {showCode ? "隐藏源码" : "查看源码"}
          </button>
        </div>
      </div>

      {/* Scene list */}
      {formatSpec.scenes && formatSpec.scenes.length > 0 && (
        <div className="p-3 bg-bg-card rounded-lg border border-fg/5">
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold mb-2">
            🎬 场景列表 ({formatSpec.scenes.length})
          </div>
          <div className="space-y-1.5">
            {formatSpec.scenes.map((s, i) => (
              <div
                key={i}
                className="flex items-start gap-2 text-xs p-2 rounded hover:bg-bg-panel transition-colors"
              >
                <span className="font-mono text-fg-subtle shrink-0 w-6">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-fg font-medium">{s.name}</div>
                  {s.description && (
                    <div className="text-[10px] text-fg-muted mt-0.5">
                      {s.description}
                    </div>
                  )}
                </div>
                <span className="text-fg-subtle shrink-0 font-mono">
                  {formatDuration(s.duration)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Manim source code */}
      {showCode && formatSpec.manim_code && (
        <div className="rounded-lg overflow-hidden border border-fg/10">
          <div className="flex items-center justify-between px-3 py-1.5 bg-bg/80 border-b border-fg/10 text-xs">
            <span className="text-fg-muted font-mono">manim · python</span>
            <button
              onClick={copy}
              className="flex items-center gap-1 text-fg-muted hover:text-fg transition-colors"
            >
              {copied ? (
                <>
                  <Check className="w-3 h-3" />
                  已复制
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" />
                  复制
                </>
              )}
            </button>
          </div>
          <SyntaxHighlighter
            language="python"
            style={atomOneDark}
            customStyle={{ fontSize: "11px", margin: 0, maxHeight: "400px" }}
          >
            {formatSpec.manim_code}
          </SyntaxHighlighter>
        </div>
      )}
    </div>
  );
}
