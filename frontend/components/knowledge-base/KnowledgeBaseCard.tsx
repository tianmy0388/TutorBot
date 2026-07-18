"use client";

import { useState } from "react";
import { AlertTriangle, Check, ChevronDown, FileText, Loader2, RefreshCw, Trash2, Upload, X } from "lucide-react";
import type { IngestionStatus, KnowledgeBaseDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_LABELS: Record<IngestionStatus, string> = {
  uploaded: "已收到",
  extracting: "正在读取",
  chunking: "正在整理",
  embedding: "准备检索",
  ready: "就绪",
  failed: "失败",
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

export function KnowledgeBaseCard({ detail, isActive, onSelect, onUpload, onRetry, onDelete, onDeleteLibrary }: KnowledgeBaseCardProps) {
  const [expanded, setExpanded] = useState(isActive);
  const [uploading, setUploading] = useState(false);

  const handleFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try { await onUpload(file); } finally { setUploading(false); event.target.value = ""; }
  };

  return (
    <article>
      <header>
        <div className="text-4xl" aria-hidden="true">📖</div>
        <div className="mt-5 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-2xl font-bold leading-tight tracking-[-0.02em]">{detail.name}</h2>
            {detail.description && <p className="mt-2 text-sm leading-6 text-fg-muted">{detail.description}</p>}
          </div>
          {isActive ? <span className="rounded bg-bg-subtle px-2 py-1 text-[11px] font-semibold" title="当前资料库">当前</span> : <button className="btn-secondary min-h-9 text-xs" onClick={onSelect} data-testid={`kb-${detail.id}-select`}>设为当前</button>}
        </div>
      </header>

      <dl className="mt-6 space-y-2 border-y border-border py-4 text-xs">
        <Property label="文档" value={`${detail.document_count} 份文档`} />
        <Property label="内容" value={`${detail.total_chunks} 块`} />
        <Property label="状态" value={`${detail.ready_count} 就绪`} />
        {detail.failed_count > 0 && <Property label="需处理" value={`${detail.failed_count} 失败`} />}
      </dl>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <label className="btn-primary min-h-10 cursor-pointer text-xs" data-testid={`kb-${detail.id}-upload`}>
          {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          上传文档
          <input type="file" accept=".pdf,.docx,.pptx,.md,.txt" className="hidden" onChange={handleFile} disabled={uploading} />
        </label>
        <button className="btn-secondary min-h-10 text-xs" onClick={() => setExpanded((value) => !value)} data-testid={`kb-${detail.id}-expand`} aria-expanded={expanded}>
          <ChevronDown className={cn("h-4 w-4 transition-transform", !expanded && "-rotate-90")} />
          {expanded ? "收起文档" : "查看文档"}
        </button>
        {!detail.is_seeded && <button className="ml-auto flex min-h-10 min-w-10 items-center justify-center rounded-md text-fg-muted hover:bg-bg-subtle hover:text-fg" onClick={onDeleteLibrary} data-testid={`kb-${detail.id}-delete`} title="删除资料库"><Trash2 className="h-4 w-4" /></button>}
      </div>

      {expanded && (
        <section className="mt-6 border-t border-border pt-5">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-xs font-semibold uppercase tracking-[0.1em] text-fg-muted">文档</h3>
            <span className="text-[11px] text-fg-subtle">PDF / DOCX / PPTX / MD / TXT</span>
          </div>
          {detail.documents.length === 0 ? <p className="mt-5 rounded-md bg-bg-subtle px-4 py-8 text-center text-xs text-fg-muted">这个资料库里还没有文档。</p> : (
            <ul className="mt-3 divide-y divide-border border-y border-border">
              {detail.documents.map((document) => {
                const ready = document.status === "ready";
                const failed = document.status === "failed";
                const terminal = ready || failed;
                return (
                  <li key={document.id} className="py-3" data-testid={`kb-doc-${document.id}`}>
                    <div className="flex items-start gap-3">
                      <FileText className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium" title={document.display_name}>{document.display_name}</p>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-fg-muted">
                          <span className="inline-flex items-center gap-1">
                            {ready ? <Check className="h-3 w-3" /> : failed ? <X className="h-3 w-3" /> : <Loader2 className="h-3 w-3 animate-spin" />}
                            {STATUS_LABELS[document.status]}
                          </span>
                          {document.chunk_count > 0 && <span>{document.chunk_count} 块</span>}
                          {document.embedding_warning && ready && <span className="inline-flex items-center gap-1" title={document.embedding_warning} data-testid={`kb-doc-${document.id}-embed-warning`}><AlertTriangle className="h-3 w-3" />仅可按文字查找</span>}
                          {document.error && <span className="max-w-full truncate" title={document.error}>{document.error}</span>}
                        </div>
                      </div>
                      {failed && <button className="flex min-h-9 min-w-9 items-center justify-center rounded text-fg-muted hover:bg-bg-subtle hover:text-fg" onClick={() => onRetry(document.id)} data-testid={`kb-doc-${document.id}-retry`} title="重新处理"><RefreshCw className="h-3.5 w-3.5" /></button>}
                      {terminal && <button className="flex min-h-9 min-w-9 items-center justify-center rounded text-fg-muted hover:bg-bg-subtle hover:text-fg" onClick={() => onDelete(document.id)} data-testid={`kb-doc-${document.id}-delete`} title="删除文档"><Trash2 className="h-3.5 w-3.5" /></button>}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}
    </article>
  );
}

function Property({ label, value }: { label: string; value: string }) {
  return <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-3"><dt className="text-fg-muted">{label}</dt><dd className="font-medium">{value}</dd></div>;
}
