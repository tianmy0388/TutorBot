"use client";

/**
 * KnowledgeBaseCard — one library row with metadata + per-document state.
 *
 * Shows:
 * - library name, description, document / chunk counts
 * - per-document status pills (uploaded / extracting / chunking / ready
 *   / failed) so the user can see ingestion progress
 * - upload + retry + delete actions
 * - "select as active" action that updates the global store
 */

import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Database,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { IngestionStatus, KnowledgeBaseDetail } from "@/lib/types";

const STATUS_LABELS: Record<IngestionStatus, string> = {
  uploaded: "已上传",
  extracting: "提取中",
  chunking: "分块中",
  embedding: "嵌入中",
  ready: "就绪",
  failed: "失败",
};

const STATUS_CLASSES: Record<IngestionStatus, string> = {
  uploaded: "bg-fg/10 text-fg-muted",
  extracting: "bg-blue-500/15 text-blue-300",
  chunking: "bg-blue-500/15 text-blue-300",
  embedding: "bg-blue-500/15 text-blue-300",
  ready: "bg-green-500/15 text-green-300",
  failed: "bg-red-500/15 text-red-300",
};

export interface KnowledgeBaseCardProps {
  detail: KnowledgeBaseDetail;
  isActive: boolean;
  onSelect: () => void;
  onUpload: (file: File) => Promise<void>;
  onRetry: (docId: string) => Promise<void>;
  onDelete: (docId: string) => Promise<void>;
  onDeleteLibrary: () => Promise<void>;
}

export function KnowledgeBaseCard({
  detail,
  isActive,
  onSelect,
  onUpload,
  onRetry,
  onDelete,
  onDeleteLibrary,
}: KnowledgeBaseCardProps) {
  const [expanded, setExpanded] = useState(isActive);
  const [uploading, setUploading] = useState(false);

  const acceptExts = ".pdf,.docx,.pptx,.md,.txt";

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await onUpload(file);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  return (
    <article
      className={cn(
        "rounded-xl border bg-bg-panel p-4 space-y-3",
        isActive ? "border-brand-500/60" : "border-fg/10",
      )}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 flex-1 min-w-0">
          <div
            className={cn(
              "w-9 h-9 rounded-lg flex items-center justify-center shrink-0",
              isActive ? "bg-brand-500/15 text-brand-300" : "bg-bg-card text-fg-muted",
            )}
          >
            <Database className="w-4 h-4" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold truncate">{detail.name}</h3>
            {detail.description && (
              <p className="text-xs text-fg-muted mt-0.5 line-clamp-2">
                {detail.description}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-fg-muted mt-1">
              <span>{detail.document_count} 份文档</span>
              <span>·</span>
              <span>{detail.total_chunks} 块</span>
              <span>·</span>
              <span>{detail.ready_count} 就绪</span>
              {detail.failed_count > 0 && (
                <>
                  <span>·</span>
                  <span className="text-red-300">
                    {detail.failed_count} 失败
                  </span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {!detail.is_seeded && (
            <button
              className="btn-secondary text-xs h-7"
              onClick={onDeleteLibrary}
              data-testid={`kb-${detail.id}-delete`}
              title="删除知识库"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
          {isActive ? (
            <span className="badge-success">当前</span>
          ) : (
            <button
              className="btn-secondary text-xs h-7"
              onClick={onSelect}
              data-testid={`kb-${detail.id}-select`}
            >
              设为当前
            </button>
          )}
          <button
            className="btn-secondary text-xs h-7"
            onClick={() => setExpanded((v) => !v)}
            data-testid={`kb-${detail.id}-expand`}
            title={expanded ? "收起" : "展开"}
          >
            <ChevronRight
              className={cn(
                "w-3.5 h-3.5 transition-transform",
                expanded && "rotate-90",
              )}
            />
          </button>
        </div>
      </header>

      {expanded && (
        <div className="space-y-2 pt-2 border-t border-fg/10">
          <div className="flex items-center gap-2">
            <label
              className="btn-primary text-xs h-8 cursor-pointer"
              data-testid={`kb-${detail.id}-upload`}
            >
              {uploading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Upload className="w-3.5 h-3.5" />
              )}
              <span className="ml-1">上传文档</span>
              <input
                type="file"
                accept={acceptExts}
                className="hidden"
                onChange={handleFile}
                disabled={uploading}
              />
            </label>
            <span className="text-[11px] text-fg-subtle">
              支持 PDF / DOCX / PPTX / Markdown / TXT
            </span>
          </div>

          {detail.documents.length === 0 ? (
            <p className="text-xs text-fg-subtle py-2">尚无文档</p>
          ) : (
            <ul className="space-y-1.5">
              {detail.documents.map((doc) => {
                const isFailed = doc.status === "failed";
                const isReady = doc.status === "ready";
                const isTerminal = isReady || isFailed;
                return (
                  <li
                    key={doc.id}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-bg-card text-xs"
                    data-testid={`kb-doc-${doc.id}`}
                  >
                    <span
                      className={cn(
                        "px-1.5 py-0.5 rounded text-[10px] font-medium",
                        STATUS_CLASSES[doc.status],
                      )}
                    >
                      {isReady ? (
                        <CheckCircle2 className="w-3 h-3 inline" />
                      ) : isFailed ? (
                        <XCircle className="w-3 h-3 inline" />
                      ) : (
                        <Loader2 className="w-3 h-3 inline animate-spin" />
                      )}{" "}
                      {STATUS_LABELS[doc.status]}
                    </span>
                    <span className="flex-1 truncate" title={doc.display_name}>
                      {doc.display_name}
                    </span>
                    {doc.chunk_count > 0 && (
                      <span className="text-fg-subtle">{doc.chunk_count} 块</span>
                    )}
                    {/* Non-fatal warning: doc is ready but no vectors
                        were produced (e.g. embedder not configured).
                        Surface it so the user knows retrieval will be
                        text-only, not semantic. */}
                    {doc.embedding_warning && isReady && (
                      <span
                        className="text-yellow-300 truncate max-w-[24ch] inline-flex items-center gap-0.5"
                        title={doc.embedding_warning}
                        data-testid={`kb-doc-${doc.id}-embed-warning`}
                      >
                        <AlertTriangle className="w-3 h-3 inline" /> 无向量
                      </span>
                    )}
                    {doc.error && (
                      <span
                        className="text-red-300 truncate max-w-[16ch]"
                        title={doc.error}
                      >
                        {doc.error}
                      </span>
                    )}
                    {isFailed && (
                      <button
                        className="btn-secondary text-[11px] h-6 px-2"
                        onClick={() => onRetry(doc.id)}
                        data-testid={`kb-doc-${doc.id}-retry`}
                        title="重试摄取"
                      >
                        <RefreshCw className="w-3 h-3" />
                      </button>
                    )}
                    {isTerminal && (
                      <button
                        className="btn-secondary text-[11px] h-6 px-2"
                        onClick={() => onDelete(doc.id)}
                        data-testid={`kb-doc-${doc.id}-delete`}
                        title="删除文档"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </article>
  );
}
