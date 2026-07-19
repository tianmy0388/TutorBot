"use client";

/**
 * CodeViewer — code resource with syntax highlighting + execution output.
 *
 * Supports:
 *  - Single code block
 *  - Multiple files (format_specific.files = [{name, language, code}, ...])
 *  - Execution output (stdout / stderr / status)
 *  - Explanation block
 *  - Copy to clipboard
 *  - Tab switcher between files
 *  - Rendered artifacts (PNG / SVG / PDF) from sandbox execution —
 *    **2026-07-08 fix (585f367d)**: pre-fix the viewer ignored
 *    ``format_specific.artifacts[]`` so matplotlib figures the user
 *    code produced (``figure_N.png``) were invisible on the right pane.
 */

import { useCallback, useMemo, useState } from "react";
import {
  Copy,
  Check,
  Terminal,
  PlayCircle,
  ChevronRight,
  AlertTriangle,
  ImageIcon,
} from "lucide-react";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs";
import type { CodeResourceFormat, Resource } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useTutorStore } from "@/lib/store";
import { ImageLightbox, type ImageArtifact } from "./ImageLightbox";

interface CodeFile {
  name: string;
  language: string;
  code: string;
}

interface CodeArtifact {
  name: string;
  path?: string;
  kind?: string;
}

const IMAGE_KINDS = new Set(["png", "jpg", "jpeg", "svg"]);

function artifactKind(artifact: CodeArtifact) {
  const explicit = (artifact.kind || "")
    .toLowerCase()
    .replace(/^image\//, "")
    .replace(/^\./, "");
  const normalized = explicit === "svg+xml" ? "svg" : explicit;
  if (IMAGE_KINDS.has(normalized)) return normalized;
  const extension = artifact.name.split(".").pop();
  const fallback =
    extension && extension !== artifact.name ? extension.toLowerCase() : "";
  return IMAGE_KINDS.has(fallback) ? fallback : normalized || fallback;
}

export function CodeViewer({ resource }: { resource: Resource }) {
  const userId = useTutorStore((s) => s.userId);
  // **2026-07-08 fix (039b4a70 trace):** the resource emitted in a
  // partial placeholders package (after a 600s timeout) has no
  // ``metadata.package_id`` set. Pre-fix the URL built below used
  // ``"_"`` as a fallback and the backend rejected it with 404
  // (no such package). Use the live ``latestPackage.package_id``
  // when available; otherwise drop the package segment entirely
  // and use the package-less artifact endpoint.
  const latestPackageId = useTutorStore(
    (s) => s.latestPackage?.package_id ?? null,
  );
  const formatSpec = (resource.format_specific ?? {}) as CodeResourceFormat;

  const [copied, setCopied] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);

  const files: CodeFile[] =
    formatSpec.files && formatSpec.files.length > 0
      ? formatSpec.files
      : [
          {
            name: resource.title + ".py",
            language: formatSpec.language || "python",
            code: formatSpec.code || resource.content || "",
          },
        ];

  const [activeFile, setActiveFile] = useState(0);
  const current = files[activeFile];

  const copy = async () => {
    if (!current) return;
    await navigator.clipboard.writeText(current.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const showExecution =
    formatSpec.execution_status &&
    formatSpec.execution_status !== "not_run";
  const executionFailed =
    formatSpec.execution_status === "failed" ||
    formatSpec.execution_status === "timeout";
  const figureExpectedButMissing =
    formatSpec.error_code === "FIGURE_EXPECTED_BUT_NOT_PRODUCED";

  // **2026-07-08 fix (585f367d):** turn each artifact into a URL
  // pointing at the backend streaming endpoint so matplotlib figures
  // produced by user code (e.g. loss-curve.png) actually render.
  // **2026-07-08 fix (039b4a70):** if the resource has no real
  // package_id (partial placeholder / pending package), use the
  // package-less endpoint instead of inserting ``"_"`` and 404'ing.
  const apiBase =
    (typeof window !== "undefined" &&
      ((window as { __TUTOR_API__?: string }).__TUTOR_API__)) ||
    (typeof process !== "undefined" &&
      process.env?.NEXT_PUBLIC_API_BASE) ||
    "/api/v1";

  const realPackageId =
    (resource.metadata?.package_id as string | undefined) ||
    latestPackageId ||
    "";
  const artifactUrl = useCallback((name: string) => {
    // **2026-07-09 fix (sess_ebb / 38a445a1 trace):** the package-
    // scoped artifact route on the backend is
    // ``/resources/packages/{user_id}/{package_id}/...`` — that is,
    // ``packages`` precedes ``{user_id}`` (see
    // ``tutor/api/routers/resources.py:277``). Pre-fix, this helper
    // emitted ``/resources/{userId}/packages/{pkgId}/...`` with the
    // two segments inverted, so the path never matched the route
    // and the browser got a 404 even when the file was on disk and
    // the resource was correctly owned. The package-less branch
    // below stays as ``/resources/{userId}/resources/{rid}/...``
    // (see line 300) — that one keeps the userId-first order.
    const encodedUser = encodeURIComponent(userId || "");
    const encodedName = encodeURIComponent(name);
    const encodedRid = encodeURIComponent(resource.resource_id);
    if (
      realPackageId &&
      !realPackageId.startsWith("pending-") &&
      !realPackageId.startsWith("partial-")
    ) {
      return `${apiBase}/resources/packages/${encodedUser}` +
        `/${encodeURIComponent(realPackageId)}` +
        `/resources/${encodedRid}/artifacts/${encodedName}`;
    }
    // No real package yet (job still running or timeout before
    // persistence). Fall back to the package-less endpoint, which
    // resolves by resource_id alone.
    return `${apiBase}/resources/${encodedUser}/resources/${encodedRid}/artifacts/${encodedName}`;
  }, [apiBase, realPackageId, resource.resource_id, userId]);

  const artifacts = useMemo<CodeArtifact[]>(
    () => (Array.isArray(formatSpec.artifacts) ? formatSpec.artifacts : []),
    [formatSpec.artifacts],
  );
  const imageArtifacts = useMemo<ImageArtifact[]>(
    () =>
      artifacts.flatMap((artifact) => {
        const kind = artifactKind(artifact);
        return IMAGE_KINDS.has(kind)
          ? [{ name: artifact.name, url: artifactUrl(artifact.name), kind }]
          : [];
      }),
    [artifactUrl, artifacts],
  );
  const imageIndexByUrl = useMemo(
    () => new Map(imageArtifacts.map((artifact, index) => [artifact.url, index])),
    [imageArtifacts],
  );

  return (
    <div className="space-y-4">
      {/* Explanation */}
      {formatSpec.explanation && (
        <div className="prose-tutor text-sm p-3 bg-bg-card rounded-lg border border-fg/5">
          {formatSpec.explanation}
        </div>
      )}

      {/* File tabs (if multi-file) */}
      {files.length > 1 && (
        <div className="flex gap-1 border-b border-fg/10">
          {files.map((f, i) => (
            <button
              key={i}
              onClick={() => setActiveFile(i)}
              className={cn(
                "px-3 py-1.5 text-xs font-mono rounded-t-md transition-colors flex items-center gap-1",
                activeFile === i
                  ? "bg-bg-card text-fg border border-fg/10 border-b-bg-card"
                  : "text-fg-muted hover:text-fg",
              )}
            >
              <ChevronRight className="w-3 h-3" />
              {f.name}
            </button>
          ))}
        </div>
      )}

      {/* Code block */}
      <div className="rounded-lg overflow-hidden border border-fg/10 shadow-md">
        <div className="flex items-center justify-between px-3 py-1.5 bg-bg/80 border-b border-fg/10 text-xs">
          <span className="flex items-center gap-2 text-fg-muted">
            <code className="text-accent font-mono">{current.name}</code>
            <span className="text-fg-subtle font-mono">{current.language}</span>
            {files.length > 1 && (
              <span className="text-fg-subtle text-[10px]">
                {activeFile + 1}/{files.length}
              </span>
            )}
          </span>
          <button
            onClick={copy}
            className="flex items-center gap-1 text-fg-muted hover:text-fg transition-colors"
          >
            {copied ? (
              <>
                <Check className="w-3 h-3 text-green-400" />
                <span className="text-green-400">已复制</span>
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
          language={current.language}
          style={atomOneDark}
          customStyle={{
            fontSize: "12px",
            margin: 0,
            padding: "12px",
            background: "#0d0d0d",
          }}
          showLineNumbers
          lineNumberStyle={{
            color: "#52525b",
            fontSize: "10px",
            paddingRight: "12px",
          }}
        >
          {current.code}
        </SyntaxHighlighter>
      </div>

      {/* Execution output */}
      {showExecution && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs">
            <Terminal className="w-3.5 h-3.5 text-fg-muted" />
            <span className="text-fg-muted">运行结果</span>
            <ExecutionStatusBadge status={formatSpec.execution_status!} />
            {formatSpec.runtime && (
              <span className="text-fg-subtle ml-2">{formatSpec.runtime}</span>
            )}
          </div>

          {formatSpec.stdout && (
            <pre className="bg-black/70 rounded-md p-3 text-xs font-mono text-green-300 whitespace-pre-wrap border border-green-900/30">
              {formatSpec.stdout}
            </pre>
          )}

          {figureExpectedButMissing && (
            <div
              role="alert"
              className="rounded-md border border-red-900/30 bg-red-950/20 p-3 text-xs text-red-300"
            >
              图片生成失败：代码声明应生成图像，但没有产生可展示的图片产物。
            </div>
          )}

          {formatSpec.stderr && (
            <pre
              className={cn(
                "bg-black/70 rounded-md p-3 text-xs font-mono whitespace-pre-wrap border flex gap-2",
                executionFailed
                  ? "text-red-300 border-red-900/30"
                  : "text-amber-300 border-amber-900/30",
              )}
            >
              <AlertTriangle
                className={cn(
                  "w-3.5 h-3.5 shrink-0 mt-0.5",
                  executionFailed ? "text-red-400" : "text-amber-400",
                )}
              />
              <span className="flex-1">{formatSpec.stderr}</span>
            </pre>
          )}
        </div>
      )}

      {/* Rendered artifacts (2026-07-08 fix: figures from matplotlib) */}
      {artifacts.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs text-fg-muted">
            <ImageIcon className="w-3.5 h-3.5" />
            <span>产物 ({artifacts.length})</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {artifacts.map((art) => (
              <ArtifactPreview
                key={`${resource.resource_id}:${artifactUrl(art.name)}`}
                artifact={art}
                url={artifactUrl(art.name)}
                onOpen={() => {
                  const imageIndex = imageIndexByUrl.get(artifactUrl(art.name));
                  if (imageIndex === undefined) return;
                  setLightboxIndex(imageIndex);
                  setLightboxOpen(true);
                }}
              />
            ))}
          </div>
        </div>
      )}

      {/* Dependencies */}
      {formatSpec.dependencies && formatSpec.dependencies.length > 0 && (
        <div className="p-3 bg-bg-card rounded-lg border border-fg/5">
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold mb-2">
            依赖
          </div>
          <div className="flex flex-wrap gap-1">
            {formatSpec.dependencies.map((d, i) => (
              <code
                key={i}
                className="text-[10px] px-1.5 py-0.5 rounded bg-bg-panel text-accent border border-fg/10"
              >
                {d}
              </code>
            ))}
          </div>
        </div>
      )}

      <ImageLightbox
        images={imageArtifacts}
        initialIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
      />
    </div>
  );
}

function ArtifactPreview({
  artifact,
  url,
  onOpen,
}: {
  artifact: CodeArtifact;
  url: string;
  onOpen(): void;
}) {
  const kind = artifactKind(artifact);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const errored = failedUrl === url;

  if (errored) {
    return (
      <div className="rounded-lg border border-fg/10 bg-bg-card p-4 text-xs text-fg-muted">
        <div className="font-mono">{artifact.name}</div>
        <div className="mt-1 text-fg-subtle">加载失败（文件可能已被清理）</div>
      </div>
    );
  }

  if (IMAGE_KINDS.has(kind)) {
    return (
      <button
        type="button"
        aria-label={`查看 ${artifact.name}`}
        onClick={onOpen}
        className="block w-full rounded-lg overflow-hidden border border-fg/10 bg-bg-card text-left hover:border-brand-500/40 transition-colors"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt={artifact.name}
          loading="lazy"
          onError={() => setFailedUrl(url)}
          className="image-artifact-preview w-full h-auto object-contain"
        />
        <div className="px-3 py-1.5 text-[10px] text-fg-muted font-mono border-t border-fg/5">
          {artifact.name}
        </div>
      </button>
    );
  }

  // pdf / unknown: link-only
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="block rounded-lg border border-fg/10 bg-bg-card p-4 text-xs hover:border-brand-500/40 transition-colors"
    >
      <div className="font-mono text-fg">{artifact.name}</div>
      <div className="mt-1 text-fg-muted">
        点击下载（{kind || "file"}）
      </div>
    </a>
  );
}

function ExecutionStatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; className: string; icon: any }> = {
    success: {
      label: "✓ 运行成功",
      className: "bg-green-950/40 text-green-300 border-green-800/40",
      icon: PlayCircle,
    },
    failed: {
      label: "✗ 运行失败",
      className: "bg-red-950/40 text-red-300 border-red-800/40",
      icon: AlertTriangle,
    },
    timeout: {
      label: "⏱ 运行超时",
      className: "bg-red-950/40 text-red-300 border-red-800/40",
      icon: AlertTriangle,
    },
    pending: {
      label: "⏳ 待运行",
      className: "bg-bg-panel text-fg-muted border-fg/10",
      icon: PlayCircle,
    },
  };
  const m = map[status] || {
    label: status,
    className: "bg-bg-panel text-fg-muted border-fg/10",
    icon: PlayCircle,
  };
  const Icon = m.icon;
  return (
    <span
      className={cn(
        "px-2 py-0.5 rounded-md text-[11px] border flex items-center gap-1",
        m.className,
      )}
    >
      <Icon className="w-3 h-3" />
      {m.label}
    </span>
  );
}
