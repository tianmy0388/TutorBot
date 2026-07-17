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

import { useState } from "react";
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
} from "lucide-react";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs";
import type { Resource } from "@/lib/types";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function VideoViewer({ resource }: { resource: Resource }) {
  const child = useTutorStore((state) =>
    Object.values(state.jobsById)
      .flatMap((job) => job.children ?? [])
      .find(
        (candidate) =>
          candidate.task_kind === "video_render" &&
          (candidate.metadata?.resource_id === resource.resource_id ||
            candidate.dedupe_key?.endsWith(`:${resource.resource_id}`)),
      ),
  );
  // **2026-07-08 fix (fdb26152 regression):** ``resource.format_specific``
  // can be undefined for partial resources (e.g. the placeholder
  // cards emitted via ``contract.partial_artifacts`` on a FAILED
  // contract). The previous code did a non-null cast and crashed at
  // ``formatSpec.video_url``. Default to an empty object so all the
  // optional-chain reads below stay safe.
  const formatSpec = (resource.format_specific ?? {}) as {
    video_url?: string;
    manim_code?: string;
    scene_class?: string;
    render_status?: string;
    duration_seconds?: number;
    render_error?: string;
    scenes?: Array<{ name: string; duration: number; description?: string }>;
    concept?: string;
    fps?: number;
    resolution?: string;
  };

  const [showCode, setShowCode] = useState(false);
  const [copied, setCopied] = useState(false);
  // **2026-07-09 fix (ada95ede trace):** when the URL 404s (e.g.
  // /static/manim/<scene>.mp4 isn't reachable because the FastAPI
  // static mount wasn't wired yet), the ``<video>`` element silently
  // renders a 0-second blank player. We surface that explicitly as
  // an error banner with a one-click retry of the original render.
  const [videoLoadFailed, setVideoLoadFailed] = useState(false);

  const isReady = !!formatSpec.video_url && !videoLoadFailed;
  // When ``formatSpec.video_url`` is set but the network failed we
  // flip the UI into the failure banner instead of the player shell.
  const effectiveRenderStatus = videoLoadFailed
    ? "failed"
    : child?.status === "failed" || child?.status === "cancelled"
      ? "failed"
      : child?.status === "succeeded"
        ? "succeeded"
        : formatSpec.render_status ?? "unknown";
  const isFailed = effectiveRenderStatus === "failed";
  const isSucceeded = effectiveRenderStatus === "succeeded" && !isReady;
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
      renderStatus === "unknown");

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
              : renderStatus === "unknown"
                ? "资源尚未完整生成（任务可能已超时）"
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
                : "渲染流程未生成可播放视频。"}
            </div>
            {formatSpec.render_error && (
              <div className="mt-2 text-xs text-red-400/80 font-mono whitespace-pre-wrap bg-red-950/40 rounded p-2 border border-red-800/30">
                {formatSpec.render_error}
              </div>
            )}
          </div>
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
