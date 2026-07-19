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
  const latestPackageId = useTutorStore(
    (s) => s.latestPackage?.package_id ?? null,
  );
  const fs = resource.format_specific || {};
  const slideTitles: string[] = Array.isArray(fs.slide_titles)
    ? (fs.slide_titles as string[])
    : [];
  const slideCount: number = Number(fs.slide_count ?? slideTitles.length ?? 0);
  const pptxPath: string | null = (fs.pptx_path as string) || null;
  const artifactKey: string | null = (fs.artifact_key as string) || null;
  const artifactRef = artifactKey || pptxPath;
  const packageId =
    (resource.metadata?.package_id as string | undefined) || latestPackageId;
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

  const downloadUrl = artifactRef && packageId
    ? `${apiBase}/resources/packages/${encodeURIComponent(userId)}/` +
      `${encodeURIComponent(packageId)}/resources/` +
      `${encodeURIComponent(resource.resource_id)}/download`
    : "";

  const triggerDownload = async () => {
    if (!artifactRef || !packageId) {
      setDownloadErr("PPT 文件或资源包信息缺失");
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
      <div className="py-4 border-y border-red-200 dark:border-border bg-red-50 dark:bg-bg-subtle px-3 text-sm">
        <div className="flex items-center gap-2 text-red-700 dark:text-fg font-medium mb-1">
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
      <div className="grid grid-cols-3 border-y border-border divide-x divide-border">
        <Stat
          icon={Presentation}
          label="幻灯片数"
          value={slideCount > 0 ? String(slideCount) : "—"}
        />
        <Stat
          icon={ListChecks}
          label="标题"
          value={String(slideTitles.length)}
        />
        <Stat
          icon={FileText}
          label="格式"
          value=".pptx"
        />
      </div>

      {/* Slide list */}
      {slideTitles.length > 0 ? (
        <div className="py-3 border-y border-border">
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
        <div className="py-3 border-y border-border text-xs text-fg-muted">
          暂无幻灯片大纲
        </div>
      )}

      {/* Download */}
      <div className="flex items-center gap-2">
        <button
          onClick={triggerDownload}
          disabled={!artifactRef || !packageId || downloading}
          className={cn(
            "btn-primary px-4 h-9 text-sm",
            (!artifactRef || !packageId || downloading) &&
              "opacity-50 cursor-not-allowed",
          )}
        >
          <Download className="w-4 h-4" />
          {downloading ? "下载中…" : "下载 .pptx"}
        </button>
        <span className="text-[10px] text-fg-subtle truncate flex-1">
          {artifactRef ? artifactRef.split(/[\\/]/).pop() : "(无文件)"}
        </span>
      </div>
      {downloadErr && (
        <div className="text-xs text-red-700 dark:text-fg">下载失败: {downloadErr}</div>
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
}: {
  icon: any;
  label: string;
  value: string;
}) {
  return (
    <div className="py-3 text-center">
      <Icon className="w-3.5 h-3.5 mx-auto mb-1 text-brand-600 dark:text-fg-muted" />
      <div className="text-base font-bold text-fg">{value}</div>
      <div className="text-[10px] text-fg-muted mt-0.5">{label}</div>
    </div>
  );
}
