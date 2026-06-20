"use client";

/**
 * PPTViewer — render a generated PowerPoint deck resource.
 *
 * Shows the slide titles list (read from format_specific) and exposes a
 * download button that hits the file-download REST endpoint.
 */

import { useState } from "react";
import { Download, Presentation, ListChecks, FileText } from "lucide-react";
import { useTutorStore } from "@/lib/store";
import type { Resource } from "@/lib/types";
import { cn } from "@/lib/utils";

interface PPTViewerProps {
  resource: Resource;
}

export function PPTViewer({ resource }: PPTViewerProps) {
  const userId = useTutorStore((s) => s.userId);
  const fs = resource.format_specific || {};
  const slideTitles: string[] = Array.isArray(fs.slide_titles)
    ? (fs.slide_titles as string[])
    : [];
  const slideCount: number = Number(fs.slide_count ?? slideTitles.length ?? 0);
  const pptxPath: string | null = (fs.pptx_path as string) || null;
  const error: string | null = (fs.error as string) || null;
  const filename: string =
    (resource.metadata?.pptx_filename as string) || `${resource.title}.pptx`;

  const [downloading, setDownloading] = useState(false);
  const [downloadErr, setDownloadErr] = useState<string | null>(null);

  // Build the absolute download URL — same-origin in dev, but use the
  // configured API base when available so it works behind a reverse proxy.
  const apiBase =
    (typeof window !== "undefined" &&
      ((window as any).__TUTOR_API__ as string | undefined)) ||
    (typeof process !== "undefined" &&
      process.env?.NEXT_PUBLIC_API_BASE) ||
    "/api/v1";

  const downloadUrl = pptxPath
    ? `${apiBase}/resources/packages/${encodeURIComponent(userId)}/` +
      `${encodeURIComponent(
        // We don't know package_id here, but the endpoint needs it; the
        // server routes via resource_id, so we look up package via store.
        // Easiest: include it from resource.metadata if available; else
        // fall back to the global "latest" package which the caller
        // knows. Resource.metadata.package_id is set by the capability
        // (we can rely on it).
        (resource.metadata?.package_id as string) || "_",
      )}/resources/${encodeURIComponent(resource.resource_id)}/download`
    : "";

  const triggerDownload = async () => {
    if (!pptxPath) {
      setDownloadErr("PPT 文件路径缺失");
      return;
    }
    setDownloading(true);
    setDownloadErr(null);
    try {
      const resp = await fetch(downloadUrl);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e: any) {
      setDownloadErr(e?.message || String(e));
    } finally {
      setDownloading(false);
    }
  };

  if (error) {
    return (
      <div className="p-4 bg-red-950/30 border border-red-800/40 rounded-lg text-sm">
        <div className="flex items-center gap-2 text-red-300 font-medium mb-1">
          <Presentation className="w-4 h-4" />
          PPT 生成失败
        </div>
        <div className="text-xs text-fg-muted">{error}</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2">
        <Stat
          icon={Presentation}
          label="幻灯片数"
          value={slideCount > 0 ? String(slideCount) : "—"}
          accent="cyan"
        />
        <Stat
          icon={ListChecks}
          label="标题"
          value={String(slideTitles.length)}
          accent="purple"
        />
        <Stat
          icon={FileText}
          label="格式"
          value=".pptx"
          accent="blue"
        />
      </div>

      {/* Slide list */}
      {slideTitles.length > 0 ? (
        <div className="p-3 bg-bg-card rounded-lg border border-fg/5">
          <div className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold mb-2 flex items-center gap-1">
            <Presentation className="w-3 h-3" />
            幻灯片大纲
          </div>
          <ol className="space-y-1 list-decimal list-inside">
            {slideTitles.map((t, i) => (
              <li
                key={i}
                className="text-[12px] text-fg leading-relaxed"
              >
                <span className="text-fg-muted mr-1">{i + 1}.</span>
                {t || "(未命名)"}
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <div className="p-3 bg-bg-card rounded-lg border border-fg/5 text-xs text-fg-muted">
          暂无幻灯片大纲
        </div>
      )}

      {/* Download */}
      <div className="flex items-center gap-2">
        <button
          onClick={triggerDownload}
          disabled={!pptxPath || downloading}
          className={cn(
            "btn-primary px-4 h-9 text-sm",
            (!pptxPath || downloading) && "opacity-50 cursor-not-allowed",
          )}
        >
          <Download className="w-4 h-4" />
          {downloading ? "下载中…" : "下载 .pptx"}
        </button>
        <span className="text-[10px] text-fg-subtle truncate flex-1">
          {pptxPath ? pptxPath.split(/[\\/]/).pop() : "(无文件)"}
        </span>
      </div>
      {downloadErr && (
        <div className="text-xs text-red-400">下载失败: {downloadErr}</div>
      )}

      {/* Markdown body (collapsed) */}
      {resource.content && (
        <details className="text-xs text-fg-muted">
          <summary className="cursor-pointer hover:text-fg">
            查看 Markdown 源文 ({resource.content.length} 字符)
          </summary>
          <pre className="mt-2 p-2 bg-bg-card border border-fg/5 rounded text-[11px] whitespace-pre-wrap max-h-60 overflow-y-auto">
            {resource.content}
          </pre>
        </details>
      )}
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  accent,
}: {
  icon: any;
  label: string;
  value: string;
  accent: "cyan" | "purple" | "blue";
}) {
  const colorMap = {
    cyan: "text-cyan-300 bg-cyan-950/30 border-cyan-800/40",
    purple: "text-purple-300 bg-purple-950/30 border-purple-800/40",
    blue: "text-blue-300 bg-blue-950/30 border-blue-800/40",
  } as const;
  return (
    <div className={cn("p-2.5 rounded-lg border text-center", colorMap[accent])}>
      <Icon className="w-3.5 h-3.5 mx-auto mb-1 opacity-70" />
      <div className="text-base font-bold text-fg">{value}</div>
      <div className="text-[10px] text-fg-muted mt-0.5">{label}</div>
    </div>
  );
}