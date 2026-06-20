"use client";

/**
 * StageIndicator — shows the current pipeline stage of an active turn.
 */

import { Loader2, CheckCircle2, Circle } from "lucide-react";
import { cn } from "@/lib/utils";

const STAGE_LABELS: Record<string, string> = {
  intent_understanding: "意图理解",
  profile_loading: "加载画像",
  knowledge_graph_query: "知识图谱查询",
  resource_planning: "资源规划",
  content_and_pedagogy: "内容生成",
  parallel_resource_generation: "多模态生成",
  quality_review: "质量审核",
  anti_hallucination: "事实核查",
  package_assembly: "组装资源包",
  path_integration: "整合学习路径",
  event_collection: "收集事件",
  event_aggregation: "聚合统计",
  assessment: "多维评估",
  adaptive_strategy: "自适应策略",
  persist_and_emit: "持久化",
  question_understanding: "问题理解",
  context_retrieval: "检索上下文",
  answer_generation: "生成解答",
  multi_modal_enrichment: "推荐补充",
  session_recording: "记录会话",
  content_generation: "内容生成",
  pedagogy_design: "教学设计",
  reading_compilation: "阅读材料生成",
  exercise_generation: "习题生成",
  mindmap_generation: "思维导图生成",
  video_concept_design: "视频分镜设计",
  video_code_generation: "Manim 代码生成",
  code_generation: "代码生成",
  quality_review_inner: "质量审核",
  fact_check: "事实核查",
};

export function StageIndicator({ currentStage }: { currentStage: string }) {
  const label = STAGE_LABELS[currentStage] || currentStage;
  return (
    <div className="flex items-center gap-2 text-xs text-fg-muted">
      <Loader2 className="w-3.5 h-3.5 animate-spin text-brand-400" />
      <span>
        阶段: <span className="text-brand-300 font-medium">{label}</span>
      </span>
    </div>
  );
}

export function StaticStages({ stages }: { stages: string[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {stages.map((s) => (
        <span
          key={s}
          className={cn(
            "px-2 py-0.5 rounded-full text-xs",
            "bg-bg-panel text-fg-muted border border-fg/5",
          )}
        >
          {STAGE_LABELS[s] || s}
        </span>
      ))}
    </div>
  );
}

export function StageRow({ stage, state }: { stage: string; state: "done" | "active" | "pending" }) {
  const label = STAGE_LABELS[stage] || stage;
  const Icon =
    state === "done" ? CheckCircle2 : state === "active" ? Loader2 : Circle;
  return (
    <div
      className={cn(
        "flex items-center gap-2 text-xs",
        state === "active" && "text-brand-300",
        state === "done" && "text-green-400",
        state === "pending" && "text-fg-subtle",
      )}
    >
      <Icon
        className={cn(
          "w-3.5 h-3.5",
          state === "active" && "animate-spin",
        )}
      />
      <span>{label}</span>
    </div>
  );
}
