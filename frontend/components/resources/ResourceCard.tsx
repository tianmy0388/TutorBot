"use client";

/**
 * ResourceCard — renders any resource by type via specialized viewers.
 *
 * Exports:
 *  - ResourceCard       : compact card used in tray
 *  - ResourceDetail     : large detail view (header + body) with type-specific viewer
 *  - ResourceEmptyDetail: placeholder shown when no resource is selected
 *  - RESOURCE_TYPE_META : type metadata for external use
 */

import {
  AlertTriangle,
  ExternalLink,
  FileText,
  Network,
  ListChecks,
  BookOpen,
  Video,
  Code2,
  Presentation,
  Clock,
  Inbox,
  Tag,
} from "lucide-react";
import { useEffect, useRef } from "react";
import { recordLearningEvent } from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import type { Resource } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DocumentViewer } from "./DocumentViewer";
import { MindMapViewer } from "./MindMapViewer";
import { ExerciseViewer } from "./ExerciseViewer";
import { ReadingViewer } from "./ReadingViewer";
import { VideoViewer } from "./VideoViewer";
import { CodeViewer } from "./CodeViewer";
import { PPTViewer } from "./PPTViewer";

export interface ResourceCardProps {
  resource: Resource;
  selected?: boolean;
  onClick?: () => void;
  compact?: boolean;
}

const TYPE_META: Record<
  Resource["type"],
  { label: string; icon: any; color: string; bgClass: string }
> = {
  document: {
    label: "课程讲解",
    icon: FileText,
    color: "text-fg-muted",
    bgClass: "bg-bg-subtle border-border",
  },
  mindmap: {
    label: "思维导图",
    icon: Network,
    color: "text-fg-muted",
    bgClass: "bg-bg-subtle border-border",
  },
  exercise: {
    label: "练习题",
    icon: ListChecks,
    color: "text-fg-muted",
    bgClass: "bg-bg-subtle border-border",
  },
  reading: {
    label: "拓展阅读",
    icon: BookOpen,
    color: "text-fg-muted",
    bgClass: "bg-bg-subtle border-border",
  },
  video: {
    label: "视频/动画",
    icon: Video,
    color: "text-fg-muted",
    bgClass: "bg-bg-subtle border-border",
  },
  code: {
    label: "代码示例",
    icon: Code2,
    color: "text-fg-muted",
    bgClass: "bg-bg-subtle border-border",
  },
  ppt: {
    label: "PPT 教案",
    icon: Presentation,
    color: "text-fg-muted",
    bgClass: "bg-bg-subtle border-border",
  },
};

export function ResourceCard({
  resource,
  selected,
  onClick,
  compact,
}: ResourceCardProps) {
  const meta = TYPE_META[resource.type];
  const Icon = meta.icon;
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left p-3 rounded-lg border transition-all",
        meta.bgClass,
        selected
          ? "ring-2 ring-brand-400 border-brand-500/60 shadow-md"
          : "border-fg/5 hover:border-fg/20 hover:shadow-sm",
        onClick && "cursor-pointer",
      )}
    >
      <div className="flex items-start gap-2">
        <Icon className={cn("w-4 h-4 mt-0.5 shrink-0", meta.color)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className={cn("text-xs font-medium", meta.color)}>
              {meta.label}
            </span>
            <span className="text-[10px] text-fg-subtle shrink-0">
              {"★".repeat(resource.difficulty || 0)}
            </span>
          </div>
          <div className="text-sm text-fg truncate">{resource.title}</div>
          {!compact && (
            <div className="flex items-center gap-3 mt-1.5 text-[10px] text-fg-subtle">
              <span className="flex items-center gap-0.5">
                <Clock className="w-3 h-3" />
                {resource.estimated_minutes} 分
              </span>
            </div>
          )}
        </div>
      </div>
    </button>
  );
}

/**
 * ResourceDetail — large view of a single resource with type-specific viewer.
 */
export function ResourceDetail({ resource }: { resource: Resource }) {
  const meta = TYPE_META[resource.type];
  const Icon = meta.icon;
  const userId = useTutorStore((s) => s.userId);
  const latestPackage = useTutorStore((s) => s.latestPackage);
  const startRef = useRef<number>(Date.now());

  useEffect(() => {
    startRef.current = Date.now();
    const packageId = latestPackage?.package_id || "";
    void recordLearningEvent({
      user_id: userId || "anonymous",
      event_type: "resource_viewed",
      target_id: resource.resource_id,
      concept_id: resource.topic || "",
      metadata: {
        resource_type: resource.type,
        resource_title: resource.title,
        package_id: packageId,
      },
    }).catch(() => undefined);

    return () => {
      const duration = Math.max(
        0,
        Math.round((Date.now() - startRef.current) / 1000),
      );
      if (duration < 8) return;
      void recordLearningEvent({
        user_id: userId || "anonymous",
        event_type: "resource_completed",
        target_id: resource.resource_id,
        concept_id: resource.topic || "",
        duration_seconds: duration,
        metadata: {
          resource_type: resource.type,
          resource_title: resource.title,
          package_id: packageId,
          completion_signal: "detail_view_duration",
        },
      }).catch(() => undefined);
    };
  }, [
    latestPackage?.package_id,
    resource.resource_id,
    resource.title,
    resource.topic,
    resource.type,
    userId,
  ]);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div
        className="px-5 py-3 border-b border-border flex items-center gap-3 bg-bg-subtle"
      >
        <span
          className={cn(
            "inline-flex items-center justify-center w-8 h-8 rounded-lg border",
            meta.bgClass,
            meta.color,
          )}
        >
          <Icon className="w-4 h-4" />
        </span>
        <div className="flex-1 min-w-0">
          <h2 className="font-semibold text-base truncate">
            {resource.title}
          </h2>
          <div className="text-[11px] text-fg-muted flex items-center gap-2 mt-0.5">
            <span>{meta.label}</span>
            <span>·</span>
            <span>难度 {"★".repeat(resource.difficulty || 0)}</span>
            <span>·</span>
            <span>
              <Clock className="w-3 h-3 inline" /> {resource.estimated_minutes} 分
            </span>
          </div>
        </div>
      </div>

      {/* Tags */}
      {resource.tags && resource.tags.length > 0 && (
        <div className="px-5 py-2 border-b border-fg/5 flex items-center gap-1.5 flex-wrap bg-bg-panel/30">
          <Tag className="w-3 h-3 text-fg-subtle" />
          {resource.tags.map((t, i) => (
            <span
              key={i}
              className="px-1.5 py-0.5 rounded text-[10px] bg-bg-panel border border-fg/10 text-fg-muted"
            >
              {t}
            </span>
          ))}
        </div>
      )}

      {hasEvidence(resource) && <ResourceEvidence resource={resource} />}

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-5">{renderByType(resource)}</div>
    </div>
  );
}

function ResourceEvidence({ resource }: { resource: Resource }) {
  const citations = resource.citations ?? [];
  const unverified = resource.unverified_claims ?? [];

  return (
    <details
      className="border-b border-border bg-bg-panel px-5 py-3"
      data-testid="resource-evidence"
    >
      <summary className="cursor-pointer text-xs font-semibold text-fg-muted hover:text-fg">来源与说明</summary>

      {citations.length > 0 && (
        <div className="mt-3">
          <div className="text-[11px] font-semibold text-fg-muted mb-1">
            引用来源
          </div>
          <div className="flex flex-wrap gap-1.5">
            {citations.map((citation, index) => {
              const c = asRecord(citation);
              const title = display(c.title || c.source || c.url || `citation-${index + 1}`);
              const url = display(c.url);
              const isWebUrl = /^https?:\/\//i.test(url);
              return isWebUrl ? (
                <a
                  key={`${title}-${index}`}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-fg/10 bg-bg-card text-[11px] text-fg-muted hover:text-fg"
                >
                  {title}
                  <ExternalLink className="w-3 h-3" />
                </a>
              ) : (
                <span
                  key={`${title}-${index}`}
                  className="inline-flex items-center px-2 py-1 rounded-md border border-fg/10 bg-bg-card text-[11px] text-fg-muted"
                >
                  {title}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {unverified.length > 0 && (
        <div className="mt-3 rounded-md border border-border bg-bg-subtle p-3 text-[11px] text-fg-muted">
          <div className="flex items-start gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <div className="space-y-0.5">
              {unverified.map((claim, index) => (
                <div key={`${claim}-${index}`}>{claim}</div>
              ))}
            </div>
          </div>
        </div>
      )}
    </details>
  );
}

/**
 * ResourceEmptyDetail — shown when no resource is selected.
 */
export function ResourceEmptyDetail() {
  return (
    <div className="h-full flex flex-col items-center justify-center text-fg-muted text-center p-12">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-md bg-bg-panel border border-border mb-4">
        <Inbox className="w-8 h-8 opacity-40" />
      </div>
      <div className="text-sm font-medium text-fg-muted">选择一项资料</div>
      <p className="text-xs text-fg-subtle mt-1 max-w-xs">
        从列表中选择一项查看完整内容，或回到学习任务整理新的资料。
      </p>
      <div className="mt-6 flex flex-wrap gap-2 justify-center text-[10px]">
        {["document", "mindmap", "exercise", "video", "code", "reading"].map(
          (t) => {
            const meta = TYPE_META[t as keyof typeof TYPE_META];
            const Icon = meta.icon;
            return (
              <div
                key={t}
                className={cn(
                  "px-2 py-1 rounded-md border flex items-center gap-1",
                  meta.bgClass,
                  meta.color,
                )}
              >
                <Icon className="w-3 h-3" />
                {meta.label}
              </div>
            );
          },
        )}
      </div>
    </div>
  );
}

function renderByType(resource: Resource): React.ReactNode {
  switch (resource.type) {
    case "document":
      return <DocumentViewer resource={resource} />;
    case "mindmap":
      return <MindMapViewer resource={resource} />;
    case "exercise":
      return <ExerciseViewer resource={resource} />;
    case "reading":
      return <ReadingViewer resource={resource} />;
    case "video":
      return <VideoViewer resource={resource} />;
    case "code":
      return <CodeViewer resource={resource} />;
    case "ppt":
      return <PPTViewer resource={resource} />;
    default:
      return (
        <pre className="text-xs whitespace-pre-wrap">{resource.content}</pre>
      );
  }
}

function hasEvidence(resource: Resource): boolean {
  return Boolean(
    (resource.citations && resource.citations.length > 0) ||
      (resource.unverified_claims && resource.unverified_claims.length > 0),
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function display(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

export const RESOURCE_TYPE_META = TYPE_META;
