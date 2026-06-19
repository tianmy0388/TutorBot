"use client";

import { useEffect, useRef } from "react";

interface ChatMessagesProps {
  sessionId: string;
}

export function ChatMessages({ sessionId }: ChatMessagesProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [sessionId]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
      <div className="max-w-3xl mx-auto">
        <div className="card text-center my-12">
          <h2 className="text-2xl font-bold mb-2">👋 欢迎使用 Tutor</h2>
          <p className="text-fg-muted">
            个性化学习资源生成多智能体系统 — Phase 1 占位界面
          </p>
          <div className="grid grid-cols-2 gap-3 mt-6 text-left">
            <div className="p-3 bg-bg-panel rounded-lg">
              <div className="font-medium text-sm">🎯 画像构建</div>
              <div className="text-xs text-fg-muted mt-1">
                通过对话自动构建 6 维学习画像
              </div>
            </div>
            <div className="p-3 bg-bg-panel rounded-lg">
              <div className="font-medium text-sm">📚 多模态资源</div>
              <div className="text-xs text-fg-muted mt-1">
                文档/思维导图/题库/视频/代码
              </div>
            </div>
            <div className="p-3 bg-bg-panel rounded-lg">
              <div className="font-medium text-sm">🛤️ 路径规划</div>
              <div className="text-xs text-fg-muted mt-1">
                基于知识图谱的个性化路径
              </div>
            </div>
            <div className="p-3 bg-bg-panel rounded-lg">
              <div className="font-medium text-sm">💬 智能辅导</div>
              <div className="text-xs text-fg-muted mt-1">
                即时多模态答疑解惑
              </div>
            </div>
          </div>
          <p className="text-xs text-fg-muted mt-6">
            在 Phase 4 将接入完整 WebSocket 流式对话。
          </p>
        </div>
      </div>
    </div>
  );
}
