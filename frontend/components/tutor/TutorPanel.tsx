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
 *  + Question understanding metadata (type / concepts / confidence)
 *
 * Falls back to a friendly empty state if no tutoring has happened yet.
 */

import { useState } from "react";
import {
  MessageCircle,
  Sparkles,
  Lightbulb,
  BookOpen,
  Code2,
  FlaskConical,
  Video,
  FileText,
  ChevronDown,
  ChevronRight,
  Star,
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
  concept: { label: "概念", color: "text-blue-300 bg-blue-950/40 border-blue-800/40" },
  method: { label: "方法", color: "text-green-300 bg-green-950/40 border-green-800/40" },
  debug: { label: "调试", color: "text-red-300 bg-red-950/40 border-red-800/40" },
  comparison: { label: "对比", color: "text-purple-300 bg-purple-950/40 border-purple-800/40" },
  practice: { label: "练习", color: "text-yellow-300 bg-yellow-950/40 border-yellow-800/40" },
  meta: { label: "元学习", color: "text-pink-300 bg-pink-950/40 border-pink-800/40" },
  other: { label: "其他", color: "text-fg-muted bg-bg-card border-fg/10" },
};

const ENRICHMENT_META: Record<
  EnrichmentType,
  { label: string; icon: any; color: string; bgClass: string }
> = {
  diagram: {
    label: "图解",
    icon: Sparkles,
    color: "text-purple-300",
    bgClass: "bg-purple-950/30 border-purple-800/30",
  },
  code_example: {
    label: "代码示例",
    icon: Code2,
    color: "text-orange-300",
    bgClass: "bg-orange-950/30 border-orange-800/30",
  },
  exercise: {
    label: "练习",
    icon: FlaskConical,
    color: "text-green-300",
    bgClass: "bg-green-950/30 border-green-800/30",
  },
  reference: {
    label: "参考资料",
    icon: BookOpen,
    color: "text-yellow-300",
    bgClass: "bg-yellow-950/30 border-yellow-800/30",
  },
  video: {
    label: "视频",
    icon: Video,
    color: "text-pink-300",
    bgClass: "bg-pink-950/30 border-pink-800/30",
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
    <div className="p-5 border-b border-fg/10 h-full flex flex-col overflow-hidden">
      <div className="flex items-center gap-2 mb-4 shrink-0">
        <MessageCircle className="w-4 h-4 text-brand-400" />
        <h2 className="font-semibold">即时答疑</h2>
        {answer.confidence > 0 && (
          <span className="ml-auto text-[10px] text-fg-muted flex items-center gap-1">
            <Star className="w-3 h-3" />
            置信 {(answer.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {/* Question understanding meta */}
        <UnderstandingMeta understanding={understanding} />

        {/* 4-layer answer */}
        <AnswerLayer
          tier={1}
          icon={Sparkles}
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
          <div className="p-3 bg-bg-card rounded-lg border border-fg/5 space-y-2">
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
          <div className="p-3 bg-bg-card rounded-lg border border-fg/5">
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
        在聊天中输入 "解释 XXX" 或 "为什么 XXX" 触发即时答疑
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
    <div className="p-3 bg-bg-card rounded-lg border border-fg/5">
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
          难度 {"★".repeat(understanding.difficulty || 0)}
        </span>
        {understanding.confidence > 0 && (
          <span className="ml-auto text-[10px] text-fg-muted">
            理解置信 {(understanding.confidence * 100).toFixed(0)}%
          </span>
        )}
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
    ring: "border-brand-700/40 bg-brand-950/20",
    icon: "text-brand-300",
    badge: "bg-brand-700/40 text-brand-200",
  },
  yellow: {
    ring: "border-yellow-700/40 bg-yellow-950/20",
    icon: "text-yellow-300",
    badge: "bg-yellow-700/40 text-yellow-200",
  },
  blue: {
    ring: "border-blue-700/40 bg-blue-950/20",
    icon: "text-blue-300",
    badge: "bg-blue-700/40 text-blue-200",
  },
  green: {
    ring: "border-green-700/40 bg-green-950/20",
    icon: "text-green-300",
    badge: "bg-green-700/40 text-green-200",
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
    <div className={cn("rounded-lg border", style.ring)}>
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
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
        <div className="px-3 pb-3 pt-1 text-[12px] text-fg leading-relaxed whitespace-pre-wrap border-t border-fg/5 mt-1">
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
        <Sparkles className="w-3.5 h-3.5 text-accent" />
        <span className="text-[10px] uppercase tracking-wider text-fg-muted font-semibold">
          多模态补充建议
        </span>
        <span className="ml-auto text-[10px] text-fg-subtle">
          {enrichments.length} 项
        </span>
      </div>
      <div className="space-y-1.5">
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
    <div className={cn("rounded-lg border", meta.bgClass)}>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
      >
        <Icon className={cn("w-3.5 h-3.5 shrink-0", meta.color)} />
        <span className={cn("text-xs font-medium truncate flex-1", meta.color)}>
          {enrichment.title || meta.label}
        </span>
        {enrichment.confidence > 0 && (
          <span className="text-[9px] text-fg-subtle shrink-0">
            {(enrichment.confidence * 100).toFixed(0)}%
          </span>
        )}
        <ChevronRight
          className={cn(
            "w-3 h-3 text-fg-muted shrink-0 transition-transform",
            expanded && "rotate-90",
          )}
        />
      </button>
      {expanded && (
        <div className="px-3 pb-2 space-y-1 border-t border-fg/5 mt-1 pt-1">
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