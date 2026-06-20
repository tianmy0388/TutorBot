"use client";

/**
 * Sidebar — session & navigation rail.
 *
 * Displays:
 *  - New session button + collapse toggle
 *  - Capability switcher (links to capability-specific quick actions)
 *  - Knowledge course picker (powered by useKG)
 *  - Footer with session id + ws connection status
 */

import {
  Plus,
  Settings,
  BookMarked,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Compass,
  MessageCircle,
  BarChart3,
  Network,
  Trash2,
  Activity,
} from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { useKG } from "@/hooks/useKG";
import { cn } from "@/lib/utils";

interface SidebarProps {
  /** May be empty during the first SSR frame — we render a placeholder. */
  sessionId: string;
  onNewSession: () => void;
  open: boolean;
  onToggle: () => void;
}

const CAPABILITY_NAV = [
  { id: "resource_generation", label: "资源生成", icon: Sparkles, color: "text-accent" },
  { id: "tutoring", label: "即时答疑", icon: MessageCircle, color: "text-brand-300" },
  { id: "assessment", label: "效果评估", icon: BarChart3, color: "text-green-400" },
  { id: "path_planning", label: "路径规划", icon: Compass, color: "text-yellow-300" },
] as const;

export function Sidebar({ sessionId, onNewSession, open, onToggle }: SidebarProps) {
  const wsConnected = useTutorStore((s) => s.wsConnected);
  const currentCapability = useTutorStore((s) => s.currentCapability);
  const setCapability = useTutorStore((s) => s.setCurrentCapability);
  const resetSession = useTutorStore((s) => s.resetSession);
  const setSettingsOpen = useTutorStore((s) => s.setSettingsOpen);
  const { courses, currentCourse, plannedPath } = useKG();

  if (!open) {
    return (
      <button
        onClick={onToggle}
        className="absolute left-2 top-2 z-10 p-2 bg-bg-panel border border-fg/10 rounded-lg hover:bg-bg-card transition-colors shadow-md"
        title="展开侧栏"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    );
  }

  return (
    <aside className="w-64 bg-bg-panel border-r border-fg/10 flex flex-col h-full">
      {/* Top: new session + collapse */}
      <div className="p-3 border-b border-fg/10 flex items-center justify-between shrink-0">
        <button
          onClick={() => {
            resetSession();
            onNewSession();
          }}
          className="btn-ghost flex-1 mr-2 text-sm"
          title="开始新会话 (清空当前聊天历史)"
        >
          <Plus className="w-4 h-4" />
          新会话
        </button>
        <button
          onClick={onToggle}
          className="p-2 hover:bg-bg-card rounded-lg text-fg-muted hover:text-fg transition-colors"
          title="收起侧栏"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      </div>

      {/* Capability nav */}
      <div className="p-3 border-b border-fg/10 shrink-0">
        <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold mb-2 px-1">
          能力
        </div>
        <nav className="space-y-1">
          {CAPABILITY_NAV.map((c) => {
            const Icon = c.icon;
            const active = currentCapability === c.id;
            return (
              <button
                key={c.id}
                onClick={() => setCapability(active ? null : c.id)}
                className={cn(
                  "w-full text-left px-3 py-2 rounded-lg text-sm flex items-center gap-2 transition-colors",
                  active
                    ? "bg-brand-600/30 text-brand-200 border border-brand-500/40"
                    : "hover:bg-bg-card text-fg-muted hover:text-fg",
                )}
              >
                <Icon className={cn("w-4 h-4", active && c.color)} />
                <span className="flex-1">{c.label}</span>
                {active && <span className="w-1.5 h-1.5 rounded-full bg-brand-400" />}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Course list (KG) */}
      <div className="p-3 border-b border-fg/10 flex-1 overflow-y-auto">
        <div className="text-[10px] uppercase tracking-wider text-fg-subtle font-semibold mb-2 px-1 flex items-center gap-1">
          <Network className="w-3 h-3" />
          课程
        </div>
        {courses.length === 0 ? (
          <p className="text-xs text-fg-subtle px-2 py-1">暂无课程</p>
        ) : (
          <nav className="space-y-0.5">
            {courses.map((c) => {
              const active = c === currentCourse;
              return (
                <button
                  key={c}
                  onClick={() =>
                    useTutorStore.setState({ currentCourse: c })
                  }
                  className={cn(
                    "w-full text-left px-3 py-1.5 rounded-md text-xs transition-colors",
                    active
                      ? "bg-bg-card text-fg"
                      : "text-fg-muted hover:text-fg hover:bg-bg-card/50",
                  )}
                >
                  <span className="truncate block">{c}</span>
                </button>
              );
            })}
          </nav>
        )}
        {plannedPath && (
          <div className="mt-3 px-1 text-[10px] text-fg-muted">
            <div className="flex items-center gap-1 mb-1">
              <Activity className="w-3 h-3" />
              当前路径
            </div>
            <div className="text-fg truncate">{plannedPath.name}</div>
            <div className="text-fg-subtle">
              {plannedPath.completed_count}/{plannedPath.nodes.length} 节点
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-fg/10 shrink-0">
        <div className="flex items-center gap-2 text-xs text-fg-muted mb-2">
          <span
            className={cn(
              "inline-block w-2 h-2 rounded-full shrink-0",
              wsConnected ? "bg-green-400 animate-pulse" : "bg-red-400",
            )}
          />
          <span>{wsConnected ? "WebSocket 已连接" : "WebSocket 未连接"}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <div className="font-mono text-[10px] text-fg-subtle truncate flex-1">
            {sessionId ? `${sessionId.slice(0, 8)}…` : "connecting…"}
          </div>
          <button
            onClick={() => setSettingsOpen(true)}
            className="p-1.5 text-fg-subtle hover:text-fg transition-colors"
            title="设置"
          >
            <Settings className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => {
              if (confirm("确定清空当前会话的所有数据吗？")) {
                resetSession();
                onNewSession();
              }
            }}
            className="p-1.5 text-fg-subtle hover:text-red-400 transition-colors"
            title="清空会话"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </aside>
  );
}