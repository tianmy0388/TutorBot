"use client";

import { useState } from "react";
import { Plus, Settings, BookMarked, ChevronLeft, ChevronRight } from "lucide-react";

interface SidebarProps {
  sessionId: string;
  onNewSession: () => void;
  open: boolean;
  onToggle: () => void;
}

export function Sidebar({ sessionId, onNewSession, open, onToggle }: SidebarProps) {
  if (!open) {
    return (
      <button
        onClick={onToggle}
        className="absolute left-2 top-2 z-10 p-2 bg-bg-panel rounded-lg hover:bg-bg-card transition-colors"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    );
  }

  return (
    <aside className="w-64 bg-bg-panel border-r border-fg/10 flex flex-col">
      <div className="p-3 border-b border-fg/10 flex items-center justify-between">
        <button onClick={onNewSession} className="btn-ghost flex-1 mr-2 text-sm">
          <Plus className="w-4 h-4" />
          新会话
        </button>
        <button onClick={onToggle} className="p-2 hover:bg-bg-card rounded-lg">
          <ChevronLeft className="w-4 h-4" />
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto p-3 space-y-1">
        <button className="w-full text-left px-3 py-2 rounded-lg hover:bg-bg-card text-sm flex items-center gap-2">
          <BookMarked className="w-4 h-4" />
          知识库
        </button>
        <button className="w-full text-left px-3 py-2 rounded-lg hover:bg-bg-card text-sm flex items-center gap-2">
          <Settings className="w-4 h-4" />
          设置
        </button>
      </nav>

      <div className="p-3 border-t border-fg/10 text-xs text-fg-muted">
        <div className="font-mono">{sessionId.slice(0, 12)}...</div>
        <div className="mt-1">Phase 1 占位 UI</div>
      </div>
    </aside>
  );
}
