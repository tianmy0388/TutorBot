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
import type { Resource } from "@/lib/types";
import {
  getJobDetail,
  getResourcePackageDetail,
  retryVideoRender,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function VideoViewer({ resource }: { resource: Resource }) {
  const latestPackage = useTutorStore((state) => state.latestPackage);
  const canonicalResource =
    latestPackage?.resources.find(
      (candidate) => candidate.resource_id === resource.resource_id,
    ) ?? resource;
  const canonicalPackageId =
    typeof canonicalResource.metadata?.package_id === "string"
      ? canonicalResource.metadata.package_id
      : "";
  const canonicalRenderJobId =
    typeof canonicalResource.format_specific?.render_job_id === "string"
      ? canonicalResource.format_specific.render_job_id
      : "";
  const child = useTutorStore((state) =>
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
  const formatSpec = (canonicalResource.format_specific ?? {}) as {
    video_url?: string;
    manim_code?: string;
    scene_class?: string;
    render_status?: string;
    duration_seconds?: number;
    render_error?: string;
    render_failure?: {
      error_code?: string;
      summary?: string;
      traceback_tail?: string[] | string;
      log_artifact_key?: string;
    };
    artifacts?: Array<{
      name?: string;
      kind?: string;
      artifact_key?: string;
    }>;
    scenes?: Array<{ name: string; duration: number; description?: string }>;
    concept?: string;
    fps?: number;
    resolution?: string;
    render_job_id?: string;
  };

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
  const [retryQueued, setRetryQueued] = useState(false);
  const [retryTracking, setRetryTracking] = useState<{
    jobId: string;
    parentJobId: string;
    packageId: string;
  } | null>(null);

  const isReady = !!formatSpec.video_url && !videoLoadFailed;
  // When ``formatSpec.video_url`` is set but the network failed we
  // flip the UI into the failure banner instead of the player shell.
  const effectiveRenderStatus = videoLoadFailed
    ? "failed"
    : formatSpec.render_status === "failed"
      ? "failed"
      : formatSpec.render_status === "ready"
        ? "ready"
        : child?.status === "failed" || child?.status === "cancelled"
          ? "failed"
          : child?.status === "succeeded"
            ? "succeeded"
            : child?.status === "pending" || child?.status === "running"
              ? child.status
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

  const failure = formatSpec.render_failure;
  const failureSummary = videoLoadFailed
    ? "视频文件无法加载"
    : failure?.summary || formatSpec.render_error || "渲染流程未生成可播放视频。";
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

  useEffect(() => {
    if (!retryTracking) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const track = async () => {
      try {
        while (!cancelled) {
          const detail = await getJobDetail(userId, retryTracking.parentJobId);
          if (cancelled) return;
          rehydrateJobFromDetail(detail);
          const current = detail.children?.find(
            (candidate) => candidate.job_id === retryTracking.jobId,
          );
          if (
            current &&
            ["succeeded", "partial", "failed", "cancelled"].includes(
              current.status,
            )
          ) {
            const persisted = await getResourcePackageDetail(
              userId,
              retryTracking.packageId,
            );
            if (!cancelled) {
              setLatestPackage(persisted);
              setRetryQueued(false);
              setRetryTracking(null);
            }
            return;
          }
          await new Promise<void>((resolve) => {
            timer = setTimeout(resolve, 1_000);
          });
        }
      } catch (error) {
        if (!cancelled) {
          setRetryError(
            error instanceof Error ? error.message : "重试状态刷新失败",
          );
        }
      }
    };
    void track();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [
    rehydrateJobFromDetail,
    retryTracking,
    setLatestPackage,
    userId,
  ]);

  const retry = async () => {
    if (!packageId || retrying) return;
    setRetrying(true);
    setRetryError("");
    setRetryQueued(false);
    try {
      const snapshot = await retryVideoRender(
        userId,
        packageId,
        canonicalResource.resource_id,
      );
      reconcileVideoRetry(snapshot);
      setRetryQueued(true);
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
          {retryQueued && (
            <div className="mt-2 text-xs text-green-300">重试任务已排队</div>
          )}
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
                  aria-label="重新渲染视频"
                  onClick={retry}
                  disabled={retrying}
                  className="inline-flex items-center gap-1 rounded border border-red-700/50 px-2 py-1 text-xs text-red-200 hover:bg-red-900/30 disabled:opacity-60"
                >
                  <RefreshCw className={cn("h-3 w-3", retrying && "animate-spin")} />
                  {retrying ? "正在提交…" : "重新渲染"}
                </button>
                {retryQueued && (
                  <span className="ml-2 text-xs text-green-300">重试任务已排队</span>
                )}
                {retryError && (
                  <div className="mt-1 text-xs text-red-300">{retryError}</div>
                )}
              </div>
            )}
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
