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
 */

import { useState } from "react";
import {
  Copy,
  Check,
  Terminal,
  PlayCircle,
  ChevronRight,
  AlertTriangle,
} from "lucide-react";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs";
import type { Resource } from "@/lib/types";
import { cn } from "@/lib/utils";

interface CodeFile {
  name: string;
  language: string;
  code: string;
}

export function CodeViewer({ resource }: { resource: Resource }) {
  const formatSpec = resource.format_specific as {
    language?: string;
    code?: string;
    explanation?: string;
    execution_status?: string;
    stdout?: string;
    stderr?: string;
    files?: CodeFile[];
    runtime?: string;
    dependencies?: string[];
  };

  const [copied, setCopied] = useState(false);

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

          {formatSpec.stderr && (
            <pre className="bg-black/70 rounded-md p-3 text-xs font-mono text-red-300 whitespace-pre-wrap border border-red-900/30 flex gap-2">
              <AlertTriangle className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />
              <span className="flex-1">{formatSpec.stderr}</span>
            </pre>
          )}
        </div>
      )}

      {/* Dependencies */}
      {formatSpec.dependencies && formatSpec.dependencies.length > 0 && (
        <div className="p-3 bg-bg-card rounded-lg border border-fg/5">
          <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold mb-2">
            📦 依赖
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
    </div>
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