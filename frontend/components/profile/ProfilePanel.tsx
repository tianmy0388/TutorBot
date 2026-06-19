"use client";

import { Brain, Target, Clock, Zap, BookOpen, Eye } from "lucide-react";

const DIMENSIONS = [
  {
    key: "knowledge_map",
    name: "知识基础",
    icon: Brain,
    description: "对各知识点的掌握程度 (0-1)",
  },
  {
    key: "cognitive_style",
    name: "认知风格",
    icon: Eye,
    description: "visual / verbal / deductive / inductive / active / reflective",
  },
  {
    key: "error_patterns",
    name: "易错点偏好",
    icon: Target,
    description: "历史错误类型与频率",
  },
  {
    key: "learning_pace",
    name: "学习节奏",
    icon: Clock,
    description: "单次时长 / 块大小 / 复习间隔",
  },
  {
    key: "motivation",
    name: "动机与目标",
    icon: Zap,
    description: "目标类型 / 紧迫度 / 自我效能",
  },
  {
    key: "modality",
    name: "模态偏好",
    icon: BookOpen,
    description: "text / video / interactive / diagram / code",
  },
];

export function ProfilePanel() {
  return (
    <div className="p-5 border-b border-fg/10">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-semibold">学习画像</h2>
        <span className="text-xs text-fg-muted bg-bg-card px-2 py-1 rounded">
          占位
        </span>
      </div>
      <p className="text-xs text-fg-muted mb-4">
        通过对话自动构建 — ≥6 维画像随学随新
      </p>

      <div className="space-y-3">
        {DIMENSIONS.map((dim) => {
          const Icon = dim.icon;
          return (
            <div
              key={dim.key}
              className="p-3 bg-bg-card rounded-lg border border-fg/5"
            >
              <div className="flex items-center gap-2 mb-1">
                <Icon className="w-4 h-4 text-brand-400" />
                <span className="text-sm font-medium">{dim.name}</span>
              </div>
              <p className="text-xs text-fg-muted">{dim.description}</p>
              <div className="mt-2 h-1.5 bg-bg-panel rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-brand-500 to-accent rounded-full"
                  style={{ width: "0%" }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
