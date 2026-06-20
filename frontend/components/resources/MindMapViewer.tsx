"use client";

/**
 * MindMapViewer — renders a Mermaid mindmap.
 *
 * Features:
 *  - Mermaid render with error display
 *  - Zoom in/out controls
 *  - Pan support
 *  - Fallback flat list if rendering fails
 */

import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";
import {
  AlertTriangle,
  ZoomIn,
  ZoomOut,
  Maximize2,
  RotateCcw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Resource } from "@/lib/types";

let mermaidInitialized = false;
function ensureMermaid() {
  if (mermaidInitialized) return;
  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    securityLevel: "loose",
    fontFamily:
      "ui-sans-serif, system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif",
    themeVariables: {
      background: "#171717",
      primaryColor: "#3b5dff",
      primaryTextColor: "#fafafa",
      primaryBorderColor: "#6086ff",
      lineColor: "#a1a1aa",
      secondaryColor: "#1f1f1f",
      tertiaryColor: "#0a0a0a",
    },
  });
  mermaidInitialized = true;
}

export function MindMapViewer({ resource }: { resource: Resource }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const dsl = (resource.format_specific?.mermaid_dsl as string) || "";
  const central =
    (resource.format_specific?.central_topic as string) || resource.title;
  const branches = (resource.format_specific?.branches as Array<{
    name: string;
    children?: string[];
  }>) || [];

  useEffect(() => {
    ensureMermaid();
    const el = containerRef.current;
    if (!el || !dsl.trim()) return;

    let cancelled = false;
    (async () => {
      try {
        const cleanDsl = dsl.trim();
        let finalDsl = cleanDsl;
        if (!/^\s*mindmap\b/i.test(finalDsl)) {
          // Treat as `graph TD` if user wrote something else
          finalDsl = `graph TD\n${central}[${central}]\n` +
            dsl
              .split("\n")
              .filter((l) => l.trim())
              .join("\n");
        }
        const id = `mmd_${resource.resource_id}`;
        const { svg } = await mermaid.render(id, finalDsl);
        if (!cancelled && el) {
          el.innerHTML = svg;
        }
      } catch (e: any) {
        if (!cancelled) {
          setError(e?.message || String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
      if (el) el.innerHTML = "";
    };
  }, [dsl, central, resource.resource_id]);

  if (!dsl.trim() && branches.length === 0) {
    return (
      <div className="text-sm text-fg-muted text-center py-8">
        思维导图内容为空。
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Header: topic + zoom controls */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="px-3 py-1.5 rounded-full bg-purple-950/40 border border-purple-800/40 text-purple-200 text-xs font-medium">
          🧠 {central}
        </div>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}
            className="p-1.5 rounded-md bg-bg-card hover:bg-bg-panel border border-fg/10 text-fg-muted hover:text-fg"
            title="缩小"
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <span className="text-xs text-fg-muted font-mono w-12 text-center">
            {(zoom * 100).toFixed(0)}%
          </span>
          <button
            onClick={() => setZoom((z) => Math.min(3, z + 0.25))}
            className="p-1.5 rounded-md bg-bg-card hover:bg-bg-panel border border-fg/10 text-fg-muted hover:text-fg"
            title="放大"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setZoom(1)}
            className="p-1.5 rounded-md bg-bg-card hover:bg-bg-panel border border-fg/10 text-fg-muted hover:text-fg"
            title="重置"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => wrapRef.current?.requestFullscreen?.()}
            className="p-1.5 rounded-md bg-bg-card hover:bg-bg-panel border border-fg/10 text-fg-muted hover:text-fg"
            title="全屏"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-950/30 border border-red-800/40 rounded-lg flex gap-2 text-xs text-red-300">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <div className="font-medium">Mermaid 渲染失败</div>
            <div className="mt-0.5 text-[10px] opacity-70 font-mono">
              {error}
            </div>
          </div>
        </div>
      )}

      <div
        ref={wrapRef}
        className="mermaid-container bg-bg-panel rounded-lg p-4 overflow-auto border border-fg/5"
      >
        <div
          ref={containerRef}
          className={cn(
            "flex justify-center transition-transform duration-200 origin-top",
          )}
          style={{ transform: `scale(${zoom})` }}
        />
      </div>

      {/* Fallback: flat branch list (also useful for accessibility) */}
      {branches.length > 0 && (
        <details className="mt-2">
          <summary className="text-xs text-fg-muted cursor-pointer hover:text-fg">
            📋 文字版分支 ({branches.length} 个)
          </summary>
          <ul className="mt-2 space-y-1 text-xs text-fg-muted">
            {branches.map((b, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="text-purple-400 shrink-0">▸</span>
                <div>
                  <span className="text-fg">{b.name}</span>
                  {b.children && b.children.length > 0 && (
                    <ul className="ml-4 mt-1 space-y-0.5">
                      {b.children.map((c, j) => (
                        <li key={j} className="text-fg-subtle">
                          · {c}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}