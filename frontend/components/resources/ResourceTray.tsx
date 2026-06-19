"use client";

import {
  FileText,
  Network,
  ListChecks,
  BookOpen,
  Video,
  Code2,
  Sparkles,
} from "lucide-react";

const RESOURCE_TYPES = [
  { key: "document", name: "课程讲解文档", icon: FileText, color: "text-blue-400" },
  { key: "mindmap", name: "知识点思维导图", icon: Network, color: "text-purple-400" },
  { key: "exercise", name: "练习题/题库", icon: ListChecks, color: "text-green-400" },
  { key: "reading", name: "拓展阅读材料", icon: BookOpen, color: "text-yellow-400" },
  { key: "video", name: "多模态视频/动画", icon: Video, color: "text-pink-400" },
  { key: "code", name: "代码实操案例", icon: Code2, color: "text-orange-400" },
];

export function ResourceTray() {
  return (
    <div className="p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-semibold flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-accent" />
          资源中心
        </h2>
        <span className="text-xs text-fg-muted bg-bg-card px-2 py-1 rounded">
          占位
        </span>
      </div>
      <p className="text-xs text-fg-muted mb-4">
        ≥6 类个性化学习资源 — 由多智能体协同生成
      </p>

      <div className="grid grid-cols-2 gap-3">
        {RESOURCE_TYPES.map((type) => {
          const Icon = type.icon;
          return (
            <div
              key={type.key}
              className="p-3 bg-bg-card rounded-lg border border-fg/5 hover:border-brand-500/30 transition-colors"
            >
              <Icon className={`w-5 h-5 mb-2 ${type.color}`} />
              <div className="text-xs font-medium">{type.name}</div>
              <div className="text-xs text-fg-muted mt-1">等待生成</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
