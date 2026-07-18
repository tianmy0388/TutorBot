"use client";

/**
 * ReadingViewer — extended reading resource with markdown body, summary,
 * tags and citations list.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { BookOpen, ExternalLink, Quote } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Resource } from "@/lib/types";

export function ReadingViewer({ resource }: { resource: Resource }) {
  const citations = (resource.format_specific?.citations as Array<{
    title?: string;
    url?: string;
    author?: string;
    year?: number;
    summary?: string;
  }>) || [];

  const summary = (resource.format_specific?.summary as string) || "";
  const tags = (resource.format_specific?.tags as string[]) || resource.tags || [];

  return (
    <div className="space-y-5">
      {/* Summary */}
      {summary && (
        <div className="px-1 py-4 bg-brand-50/60 dark:bg-bg-subtle border-y border-brand-200 dark:border-border">
          <div className="flex items-center gap-2 mb-2 text-brand-700 dark:text-fg-muted text-xs font-semibold">
            <Quote className="w-3.5 h-3.5" />
            一句话摘要
          </div>
          <p className="text-sm text-fg leading-relaxed">{summary}</p>
        </div>
      )}

      {/* Tags */}
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((t, i) => (
            <span
              key={i}
              className="px-2 py-0.5 rounded-md text-[10px] bg-bg-panel border border-fg/10 text-fg-muted"
            >
              #{t}
            </span>
          ))}
        </div>
      )}

      {/* Main markdown body */}
      <div className="prose-tutor">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
        >
          {resource.content || ""}
        </ReactMarkdown>
      </div>

      {/* Citations */}
      {citations.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2 text-fg-muted">
            <BookOpen className="w-3.5 h-3.5" />
            参考资料 ({citations.length})
          </h3>
          <ol className="space-y-2">
            {citations.map((c, i) => (
              <li
                key={i}
                className="text-xs text-fg-muted py-3 border-t border-border"
              >
                <div className="flex items-start gap-2">
                  <span className="text-fg-subtle font-mono shrink-0 mt-0.5">
                    [{i + 1}]
                  </span>
                  <div className="flex-1 min-w-0">
                    {c.url ? (
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-brand-700 hover:text-brand-800 dark:text-fg-muted dark:hover:text-fg inline-flex items-center gap-1"
                      >
                        <span className="truncate">{c.title || c.url}</span>
                        <ExternalLink className="w-3 h-3 shrink-0" />
                      </a>
                    ) : (
                      <span className="text-fg">{c.title || "(无标题)"}</span>
                    )}
                    {(c.author || c.year) && (
                      <div className="text-[10px] text-fg-subtle mt-0.5">
                        {c.author}
                        {c.year && ` · ${c.year}`}
                      </div>
                    )}
                    {c.summary && (
                      <div className="text-[11px] text-fg-muted mt-1 italic">
                        {c.summary}
                      </div>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
