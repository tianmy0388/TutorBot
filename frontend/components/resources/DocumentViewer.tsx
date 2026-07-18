"use client";

/**
 * DocumentViewer — renders a document resource (Markdown + LaTeX).
 *
 * If format_specific.sections is present, render a sectioned layout with a
 * sticky table of contents on the right and per-section key-points callouts.
 * Otherwise fall back to a single-column markdown render.
 */

import { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs";
import { ChevronRight, Target, BookOpen, List } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Resource } from "@/lib/types";

const markdownComponents = {
  code: ({ className, children, ...props }: { className?: string; children?: React.ReactNode }) => {
    const isInline = !className;
    if (isInline) {
      return (
        <code className="bg-bg-panel px-1.5 py-0.5 rounded text-accent text-xs" {...props}>
          {children}
        </code>
      );
    }
    const lang = (className || "").replace("language-", "") || "text";
    return (
      <SyntaxHighlighter language={lang} style={atomOneDark} customStyle={{ fontSize: "12px", margin: "12px 0", borderRadius: "8px" }}>
        {String(children).replace(/\n$/, "")}
      </SyntaxHighlighter>
    );
  },
  img: ({ src, alt, ...props }: React.ImgHTMLAttributes<HTMLImageElement>) => {
    const imageSrc = typeof src === "string" ? src : "";
    const isAllowed = /^https?:\/\//i.test(imageSrc) || imageSrc.startsWith("/api/");
    if (!isAllowed) {
      return <span className="text-xs text-fg-muted">图片未提供</span>;
    }
    return <img src={imageSrc} alt={alt || ""} {...props} />;
  },
};

export function DocumentViewer({ resource }: { resource: Resource }) {
  const sections = (resource.format_specific?.sections as Array<{
    title: string;
    content: string;
    key_points?: string[];
  }>) || [];

  if (sections.length === 0) {
    return (
      <div className="prose-tutor">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={markdownComponents}
        >
          {resource.content || ""}
        </ReactMarkdown>
      </div>
    );
  }

  return <SectionedDocument sections={sections} />;
}

function SectionedDocument({
  sections,
}: {
  sections: Array<{ title: string; content: string; key_points?: string[] }>;
}) {
  const [activeIdx, setActiveIdx] = useState(0);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Track which section is in view
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const headings = container.querySelectorAll("[data-section-anchor]");
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const idx = Number((entry.target as HTMLElement).dataset.sectionAnchor);
            setActiveIdx(idx);
          }
        }
      },
      { rootMargin: "-20% 0px -60% 0px", threshold: 0 },
    );
    headings.forEach((h) => observer.observe(h));
    return () => observer.disconnect();
  }, []);

  return (
    <div className="grid grid-cols-[1fr_180px] gap-6">
      {/* Main content */}
      <div ref={containerRef} className="space-y-6 min-w-0">
        {sections.map((s, i) => (
          <section
            key={i}
            data-section-anchor={i}
            id={`doc-section-${i}`}
            className="scroll-mt-6"
          >
            <h2 className="text-xl font-bold mb-3 flex items-center gap-2">
              <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-brand-600/30 text-brand-300 text-xs font-mono">
                {i + 1}
              </span>
              {s.title}
            </h2>
            <div className="prose-tutor">
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={markdownComponents}
              >
                {s.content || ""}
              </ReactMarkdown>
            </div>
            {s.key_points && s.key_points.length > 0 && (
              <div className="mt-4 p-3 bg-brand-950/30 border border-brand-800/30 rounded-lg">
                <div className="text-xs font-semibold text-brand-300 mb-2 flex items-center gap-1.5">
                  <Target className="w-3.5 h-3.5" />
                  关键点
                </div>
                <ul className="space-y-1.5">
                  {s.key_points.map((p, j) => (
                    <li key={j} className="text-xs text-fg flex gap-2">
                      <span className="text-brand-400 shrink-0 font-mono">
                        {String(j + 1).padStart(2, "0")}
                      </span>
                      <span>{p}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        ))}
      </div>

      {/* TOC sidebar */}
      <aside className="hidden lg:block">
        <div className="sticky top-4 p-3 bg-bg-card rounded-lg border border-fg/5">
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold mb-2 flex items-center gap-1">
            <List className="w-3 h-3" />
            目录
          </div>
          <nav className="space-y-1">
            {sections.map((s, i) => (
              <a
                key={i}
                href={`#doc-section-${i}`}
                onClick={() => setActiveIdx(i)}
                className={cn(
                  "block text-xs px-2 py-1 rounded transition-colors leading-snug",
                  activeIdx === i
                    ? "bg-brand-600/30 text-brand-200 border-l-2 border-brand-400"
                    : "text-fg-muted hover:text-fg hover:bg-bg/60",
                )}
              >
                <span className="font-mono text-fg-subtle mr-1.5">
                  {String(i + 1).padStart(2, "0")}
                </span>
                {s.title}
              </a>
            ))}
          </nav>
        </div>
      </aside>
    </div>
  );
}
