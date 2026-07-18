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
  CheckCircle2,
  ExternalLink,
  FileText,
  Network,
  ListChecks,
  BookOpen,
  Video,
  Code2,
  Presentation,
  Clock,
  Star,
  Sparkles,
  Inbox,
  Tag,
  ShieldCheck,
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
  { label: string; icon: any; color: string; bgClass: string; gradient: string }
> = {
  document: {
    label: "课程讲解",
    icon: FileText,
    color: "text-blue-300",
    bgClass: "bg-blue-950/30 border-blue-800/30",
    gradient: "from-blue-500/20 to-blue-600/5",
  },
  mindmap: {
    label: "思维导图",
    icon: Network,
    color: "text-purple-300",
    bgClass: "bg-purple-950/30 border-purple-800/30",
    gradient: "from-purple-500/20 to-purple-600/5",
  },
  exercise: {
    label: "练习题",
    icon: ListChecks,
    color: "text-green-300",
    bgClass: "bg-green-950/30 border-green-800/30",
    gradient: "from-green-500/20 to-green-600/5",
  },
  reading: {
    label: "拓展阅读",
    icon: BookOpen,
    color: "text-yellow-300",
    bgClass: "bg-yellow-950/30 border-yellow-800/30",
    gradient: "from-yellow-500/20 to-yellow-600/5",
  },
  video: {
    label: "视频/动画",
    icon: Video,
    color: "text-pink-300",
    bgClass: "bg-pink-950/30 border-pink-800/30",
    gradient: "from-pink-500/20 to-pink-600/5",
  },
  code: {
    label: "代码示例",
    icon: Code2,
    color: "text-orange-300",
    bgClass: "bg-orange-950/30 border-orange-800/30",
    gradient: "from-orange-500/20 to-orange-600/5",
  },
  ppt: {
    label: "PPT 教案",
    icon: Presentation,
    color: "text-cyan-300",
    bgClass: "bg-cyan-950/30 border-cyan-800/30",
    gradient: "from-cyan-500/20 to-cyan-600/5",
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
              <span className="flex items-center gap-0.5">
                <Star className="w-3 h-3" />
                {(resource.confidence_score * 100).toFixed(0)}%
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
        className={cn(
          "px-5 py-3 border-b border-fg/10 flex items-center gap-3",
          `bg-gradient-to-r ${meta.gradient}`,
        )}
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
            <span>·</span>
            <span>
              <Star className="w-3 h-3 inline" />{" "}
              {(resource.confidence_score * 100).toFixed(0)}% 置信
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
  const review = asRecord(resource.review);
  const safety = asRecord(resource.safety);
  const citations = resource.citations ?? [];
  const unverified = resource.unverified_claims ?? [];
  const reviewVerdict = display(review.verdict || "n/a");
  const safetyVerdict = display(safety.verdict || "n/a");

  return (
    <div
      className="px-5 py-3 border-b border-fg/5 bg-bg-panel/20 space-y-3"
      data-testid="resource-evidence"
    >
      <div className="flex items-center gap-2 text-xs font-semibold">
        <ShieldCheck className="w-3.5 h-3.5 text-brand-300" />
        <span>可信证据</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        <EvidenceTile
          icon={CheckCircle2}
          label="质量审核"
          value={`${reviewVerdict} · ${score(review.quality_score)}`}
        />
        <EvidenceTile
          icon={ShieldCheck}
          label="安全检查"
          value={`${safetyVerdict} · ${display(safety.risk_level || "n/a")}`}
        />
        <EvidenceTile
          icon={Sparkles}
          label="生成 Agent"
          value={resource.generated_by?.join(", ") || "n/a"}
        />
      </div>

      {citations.length > 0 && (
        <div>
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
        <div className="rounded-md border border-yellow-500/25 bg-yellow-500/10 p-2 text-[11px] text-yellow-100">
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
    </div>
  );
}

function EvidenceTile({
  icon: Icon,
  label,
  value,
}: {
  icon: any;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-fg/10 bg-bg-card/50 p-2 min-w-0">
      <div className="flex items-center gap-1.5 text-[10px] text-fg-subtle">
        <Icon className="w-3 h-3" />
        {label}
      </div>
      <div className="text-[11px] text-fg-muted mt-1 truncate" title={value}>
        {value}
      </div>
    </div>
  );
}

/**
 * ResourceEmptyDetail — shown when no resource is selected.
 */
export function ResourceEmptyDetail() {
  return (
    <div className="h-full flex flex-col items-center justify-center text-fg-muted text-center p-12">
      <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-bg-panel border border-fg/10 mb-4">
        <Inbox className="w-8 h-8 opacity-40" />
      </div>
      <div className="text-sm font-medium text-fg-muted">资源详情区</div>
      <p className="text-xs text-fg-subtle mt-1 max-w-xs">
        从左侧资源列表中选择一项以查看完整内容；或在聊天中发送"系统学习 XXX"开始生成
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
      (resource.unverified_claims && resource.unverified_claims.length > 0) ||
      Object.keys(asRecord(resource.review)).length > 0 ||
      Object.keys(asRecord(resource.safety)).length > 0,
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

function score(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return "n/a";
  return `${Math.round(numeric * 100)}%`;
}

export const RESOURCE_TYPE_META = TYPE_META;
