"use client";

/**
 * TutorPanel — displays the latest tutoring result.
 *
 * Layout (4-layer answer card):
 *  1. TL;DR         — one-line summary
 *  2. Intuition     — intuitive / analogy-based explanation
 *  3. Principle     — formal principle / definition
 *  4. Example       — worked example
 *  + Follow-up suggestion
 *  + Related concepts
 *  + Sources
 *  + Enrichment suggestions (diagram / code / exercise / reference / video)
 *  + Question context (type / concepts)
 *
 * Falls back to a friendly empty state if no tutoring has happened yet.
 */

import { useState } from "react";
import {
  MessageCircle,
  Lightbulb,
  BookOpen,
  Code2,
  FlaskConical,
  Video,
  FileText,
  ChevronDown,
  ChevronRight,
  ArrowRight,
  HelpCircle,
  Tag,
} from "lucide-react";
import { useTutorStore } from "@/lib/store";
import type {
  EnrichmentSuggestion,
  EnrichmentType,
  QuestionType,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const QUESTION_TYPE_META: Record<
  QuestionType,
  { label: string; color: string }
> = {
  concept: { label: "概念", color: "text-brand-700 dark:text-fg border-border" },
  method: { label: "方法", color: "text-brand-700 dark:text-fg border-border" },
  debug: { label: "调试", color: "text-brand-700 dark:text-fg border-border" },
  comparison: { label: "对比", color: "text-brand-700 dark:text-fg border-border" },
  practice: { label: "练习", color: "text-brand-700 dark:text-fg border-border" },
  meta: { label: "元学习", color: "text-brand-700 dark:text-fg border-border" },
  other: { label: "其他", color: "text-fg-muted border-border" },
};

const ENRICHMENT_META: Record<
  EnrichmentType,
  { label: string; icon: any; color: string; bgClass: string }
> = {
  diagram: {
    label: "图解",
    icon: FileText,
    color: "text-fg-muted",
    bgClass: "border-border",
  },
  code_example: {
    label: "代码示例",
    icon: Code2,
    color: "text-fg-muted",
    bgClass: "border-border",
  },
  exercise: {
    label: "练习",
    icon: FlaskConical,
    color: "text-fg-muted",
    bgClass: "border-border",
  },
  reference: {
    label: "参考资料",
    icon: BookOpen,
    color: "text-fg-muted",
    bgClass: "border-border",
  },
  video: {
    label: "视频",
    icon: Video,
    color: "text-fg-muted",
    bgClass: "border-border",
  },
};

export function TutorPanel() {
  const understanding = useTutorStore((s) => s.latestUnderstanding);
  const answer = useTutorStore((s) => s.latestTutorAnswer);
  const enrichments = useTutorStore((s) => s.latestEnrichments);

  if (!understanding || !answer) {
    return <EmptyTutor />;
  }

  return (
    <div className="p-4 h-full flex flex-col overflow-hidden">
      <div className="flex items-center gap-2 mb-4 shrink-0">
        <MessageCircle className="w-4 h-4 text-brand-400" />
        <h2 className="font-semibold">问题讲解</h2>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {/* Question understanding meta */}
        <UnderstandingMeta understanding={understanding} />

        {/* 4-layer answer */}
        <AnswerLayer
          tier={1}
          icon={FileText}
          label="一句话总结"
          tone="brand"
          content={answer.tldr}
        />
        <AnswerLayer
          tier={2}
          icon={Lightbulb}
          label="直觉理解"
          tone="yellow"
          content={answer.intuition}
        />
        <AnswerLayer
          tier={3}
          icon={BookOpen}
          label="原理/定义"
          tone="blue"
          content={answer.principle}
        />
        <AnswerLayer
          tier={4}
          icon={FlaskConical}
          label="示例"
          tone="green"
          content={answer.example}
        />

        {/* Follow-up + related */}
        {(answer.follow_up_suggestion ||
          (answer.related_concepts && answer.related_concepts.length > 0)) && (
          <div className="py-3 border-t border-border space-y-2">
            {answer.follow_up_suggestion && (
              <div className="flex items-start gap-2 text-xs">
                <HelpCircle className="w-3.5 h-3.5 text-brand-400 mt-0.5 shrink-0" />
                <div>
                  <div className="text-fg-muted text-[10px] uppercase tracking-wider mb-1">
                    建议追问
                  </div>
                  <div className="text-fg">{answer.follow_up_suggestion}</div>
                </div>
              </div>
            )}
            {answer.related_concepts && answer.related_concepts.length > 0 && (
              <div className="flex items-start gap-2 text-xs">
                <Tag className="w-3.5 h-3.5 text-accent mt-0.5 shrink-0" />
                <div className="flex-1">
                  <div className="text-fg-muted text-[10px] uppercase tracking-wider mb-1">
                    相关概念
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {answer.related_concepts.map((c, i) => (
                      <code
                        key={i}
                        className="text-[10px] px-1.5 py-0.5 rounded bg-bg-panel border border-fg/10 text-accent font-mono"
                      >
                        {c}
                      </code>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Enrichment suggestions */}
        {enrichments && enrichments.length > 0 && (
          <EnrichmentList enrichments={enrichments} />
        )}

        {/* Sources */}
        {answer.sources && answer.sources.length > 0 && (
          <div className="py-3 border-t border-border">
            <div className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold mb-2">
              引用来源
            </div>
            <ul className="space-y-1">
              {answer.sources.map((s, i) => (
                <li key={i} className="text-[11px] text-fg-muted">
                  · {s}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyTutor() {
  return (
    <div className="p-5 border-b border-fg/10 h-full flex flex-col items-center justify-center text-center text-fg-muted text-xs space-y-2 px-2">
      <MessageCircle className="w-8 h-8 opacity-30" />
      <p>暂无答疑结果</p>
      <p className="text-fg-subtle leading-relaxed">
        写下想弄懂的问题，讲解会整理在这里
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Question understanding meta block
// ---------------------------------------------------------------------------

function UnderstandingMeta({
  understanding,
}: {
  understanding: NonNullable<ReturnType<typeof useTutorStore.getState>["latestUnderstanding"]>;
}) {
  const qMeta = QUESTION_TYPE_META[understanding.question_type] || QUESTION_TYPE_META.other;

  return (
    <div className="py-3 border-t border-border">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span
          className={cn(
            "px-2 py-0.5 rounded text-[10px] border font-medium",
            qMeta.color,
          )}
        >
          {qMeta.label}
        </span>
        <span className="text-[10px] text-fg-muted">
          难度 {understanding.difficulty || 0}
        </span>
      </div>
      {understanding.student_intent && (
        <div className="text-[11px] text-fg-muted italic">
          意图: {understanding.student_intent}
        </div>
      )}
      {understanding.concepts && understanding.concepts.length > 0 && (
        <div className="mt-2 flex items-center gap-1 flex-wrap">
          <Tag className="w-3 h-3 text-fg-subtle" />
          {understanding.concepts.map((c, i) => (
            <code
              key={i}
              className="text-[10px] px-1.5 py-0.5 rounded bg-bg-panel border border-fg/10 text-accent font-mono"
            >
              {c}
            </code>
          ))}
        </div>
      )}
      {understanding.follow_up_questions &&
        understanding.follow_up_questions.length > 0 && (
          <details className="mt-2 text-[10px]">
            <summary className="cursor-pointer text-fg-muted hover:text-fg">
              可能的追问方向 ({understanding.follow_up_questions.length})
            </summary>
            <ul className="mt-1 space-y-0.5 text-fg-subtle">
              {understanding.follow_up_questions.map((q, i) => (
                <li key={i}>· {q}</li>
              ))}
            </ul>
          </details>
        )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Answer layer (1-4)
// ---------------------------------------------------------------------------

const TONE_STYLE: Record<
  string,
  { ring: string; icon: string; badge: string }
> = {
  brand: {
    ring: "border-border",
    icon: "text-fg-muted",
    badge: "text-brand-700 dark:text-fg border border-border",
  },
  yellow: {
    ring: "border-border",
    icon: "text-fg-muted",
    badge: "text-fg-muted border border-border",
  },
  blue: {
    ring: "border-border",
    icon: "text-fg-muted",
    badge: "text-fg-muted border border-border",
  },
  green: {
    ring: "border-border",
    icon: "text-fg-muted",
    badge: "text-fg-muted border border-border",
  },
};

function AnswerLayer({
  tier,
  icon: Icon,
  label,
  tone,
  content,
}: {
  tier: number;
  icon: any;
  label: string;
  tone: "brand" | "yellow" | "blue" | "green";
  content: string;
}) {
  const [expanded, setExpanded] = useState(true);
  const style = TONE_STYLE[tone];

  if (!content || content.trim() === "") return null;

  return (
    <div className={cn("border-b", style.ring)}>
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full py-2.5 flex items-center gap-2 text-left"
      >
        <span
          className={cn(
            "inline-flex items-center justify-center w-5 h-5 rounded text-[10px] font-bold shrink-0",
            style.badge,
          )}
        >
          {tier}
        </span>
        <Icon className={cn("w-3.5 h-3.5 shrink-0", style.icon)} />
        <span className="text-xs font-medium">{label}</span>
        <ChevronDown
          className={cn(
            "w-3 h-3 text-fg-muted ml-auto transition-transform",
            !expanded && "-rotate-90",
          )}
        />
      </button>
      {expanded && (
        <div className="pb-3 pt-2 text-[12px] text-fg leading-relaxed whitespace-pre-wrap border-t border-border">
          {content}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Enrichment list
// ---------------------------------------------------------------------------

function EnrichmentList({
  enrichments,
}: {
  enrichments: EnrichmentSuggestion[];
}) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <BookOpen className="w-3.5 h-3.5 text-fg-muted" />
        <span className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold">
          补充材料
        </span>
        <span className="ml-auto text-[10px] text-fg-subtle">
          {enrichments.length} 项
        </span>
      </div>
      <div className="border-t border-border">
        {enrichments.map((e, i) => (
          <EnrichmentCard key={i} enrichment={e} />
        ))}
      </div>
    </div>
  );
}

function EnrichmentCard({ enrichment }: { enrichment: EnrichmentSuggestion }) {
  const [expanded, setExpanded] = useState(false);
  const meta = ENRICHMENT_META[enrichment.type] || ENRICHMENT_META.reference;
  const Icon = meta.icon;

  return (
    <div className={cn("border-b", meta.bgClass)}>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full py-2.5 flex items-center gap-2 text-left"
      >
        <Icon className={cn("w-3.5 h-3.5 shrink-0", meta.color)} />
        <span className={cn("text-xs font-medium truncate flex-1", meta.color)}>
          {enrichment.title || meta.label}
        </span>
        <ChevronRight
          className={cn(
            "w-3 h-3 text-fg-muted shrink-0 transition-transform",
            expanded && "rotate-90",
          )}
        />
      </button>
      {expanded && (
        <div className="pb-3 space-y-1 border-t border-border pt-2">
          <div className="text-[11px] text-fg leading-relaxed whitespace-pre-wrap">
            {enrichment.content}
          </div>
          {enrichment.rationale && (
            <div className="text-[10px] text-fg-muted italic flex items-start gap-1">
              <ArrowRight className="w-3 h-3 mt-0.5 shrink-0" />
              <span>{enrichment.rationale}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
