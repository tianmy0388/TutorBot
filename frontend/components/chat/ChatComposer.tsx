"use client";

import { useState } from "react";
import { Send } from "lucide-react";

interface ChatComposerProps {
  sessionId: string;
}

export function ChatComposer({ sessionId }: ChatComposerProps) {
  const [message, setMessage] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!message.trim() || isStreaming) return;
    // 占位 — 完整实现在 Phase 4
    console.log("submit:", { sessionId, message });
    setMessage("");
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="border-t border-fg/10 bg-bg-panel/50 backdrop-blur px-6 py-4"
    >
      <div className="flex gap-3 items-end">
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSubmit(e);
            }
          }}
          placeholder="问我任何学习相关的问题… (Shift+Enter 换行)"
          rows={2}
          className="flex-1 bg-bg-card border border-fg/10 rounded-xl px-4 py-3 resize-none focus:outline-none focus:border-brand-500 transition-colors"
        />
        <button
          type="submit"
          disabled={!message.trim() || isStreaming}
          className="btn-primary h-12 px-5 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Send className="w-4 h-4" />
          发送
        </button>
      </div>
      <p className="text-xs text-fg-muted mt-2">
        Phase 1 占位 — Phase 4 接入完整 WebSocket 流式对话
      </p>
    </form>
  );
}
