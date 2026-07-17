"use client";

/**
 * HomePage — TutorBot's main 3-column learning workspace (2026-07 redesign).
 *
 * Layout:
 *   - Sidebar (left): sessions, capabilities, courses, history
 *   - Center column: chat stream + composer
 *   - Right column: profile / path / resource / tutor / assessment tabs
 *
 * The header bar is intentionally minimal — the Sidebar already carries
 * brand identity, so the workspace header holds just a domain subtitle
 * and a status pill. Result: more whitespace, less noise.
 */

import { useEffect, useRef, useState } from "react";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatMessages } from "@/components/chat/ChatMessages";
import { JobTray } from "@/components/chat/JobTray";
import { ProfilePanel } from "@/components/profile/ProfilePanel";
import { ResourceTray } from "@/components/resources/ResourceTray";
import { ResourceDetail, ResourceEmptyDetail } from "@/components/resources/ResourceCard";
import { PathVisualizer } from "@/components/kg/PathVisualizer";
import { TutorPanel } from "@/components/tutor/TutorPanel";
import { AssessmentPanel } from "@/components/assessment/AssessmentPanel";
import { Sidebar } from "@/components/layout/Sidebar";
import { SettingsModal } from "@/components/layout/SettingsModal";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTutorStore } from "@/lib/store";
import { isJobTerminal } from "@/lib/job-reducer";
import {
  Brain,
  Sparkles,
  TrendingUp,
  Activity,
  Layers,
  MessageCircle,
  BarChart3,
} from "lucide-react";
import { cn } from "@/lib/utils";

type RightPane = "profile" | "path" | "resource" | "tutor" | "assessment";

export default function HomePage() {
  const sessionId = useTutorStore((s) => s.sessionId);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [rightPane, setRightPane] = useState<RightPane>("profile");
  const userInitiatedPane = useRef(false);

  const hydrateTheme = useTutorStore((s) => s.hydrateTheme);
  useEffect(() => {
    hydrateTheme();
  }, [hydrateTheme]);

  const hydrateSessionId = useTutorStore((s) => s.hydrateSessionId);
  const sessionIdRestored = useTutorStore((s) => s.sessionId);
  const userIdRestored = useTutorStore((s) => s.userId);
  const loadConversationAggregate = useTutorStore(
    (s) => s.loadConversationAggregate,
  );
  const messagesLength = useTutorStore((s) => s.messages.length);
  const sessionRestoreRef = useRef(false);
  useEffect(() => {
    hydrateSessionId();
  }, [hydrateSessionId]);

  useEffect(() => {
    if (sessionRestoreRef.current) return;
    if (!sessionIdRestored || sessionIdRestored.length === 0) return;
    if (messagesLength > 0) return;
    if (!userIdRestored) return;
    sessionRestoreRef.current = true;
    void loadConversationAggregate(userIdRestored, sessionIdRestored).catch(
      (err) => {
        // eslint-disable-next-line no-console
        console.warn(
          "[page] mount-time loadConversationAggregate failed; falling back to in-memory state",
          err,
        );
      },
    );
  }, [
    sessionIdRestored,
    userIdRestored,
    messagesLength,
    loadConversationAggregate,
  ]);

  useWebSocket();

  const latestPackage = useTutorStore((s) => s.latestPackage);
  const latestTutorAnswer = useTutorStore((s) => s.latestTutorAnswer);
  const latestAssessment = useTutorStore((s) => s.latestAssessment);
  const selectedResourceId = useTutorStore((s) => s.resourceSelection.selectedResourceId);
  const plannedPath = useTutorStore((s) => s.plannedPath);
  const hasActiveJob = useTutorStore((s) =>
    Object.values(s.jobsById).some((job) => !isJobTerminal(job)),
  );

  const lastPackageId = useRef<string | null>(null);
  const lastTutorId = useRef<string | null>(null);
  const lastAssessmentId = useRef<string | null>(null);

  useEffect(() => {
    if (!latestPackage) return;
    if (lastPackageId.current === latestPackage.package_id) return;
    lastPackageId.current = latestPackage.package_id;
    if (latestPackage.resources.length > 0 && !userInitiatedPane.current) {
      setRightPane("resource");
    }
  }, [latestPackage]);

  useEffect(() => {
    if (!latestTutorAnswer) return;
    const id = latestTutorAnswer.full_markdown?.slice(0, 32) || "x";
    if (lastTutorId.current === id) return;
    lastTutorId.current = id;
    if (!userInitiatedPane.current) {
      setRightPane("tutor");
    }
  }, [latestTutorAnswer]);

  useEffect(() => {
    if (!latestAssessment) return;
    const id = latestAssessment.created_at || "x";
    if (lastAssessmentId.current === id) return;
    lastAssessmentId.current = id;
    if (!userInitiatedPane.current) {
      setRightPane("assessment");
    }
  }, [latestAssessment]);

  useEffect(() => {
    if (selectedResourceId) {
      setRightPane("resource");
    }
  }, [selectedResourceId]);

  const selectedResource = latestPackage?.resources.find(
    (r) => r.resource_id === selectedResourceId,
  );

  const handleTabClick = (pane: RightPane) => {
    userInitiatedPane.current = true;
    setRightPane(pane);
  };

  return (
    <div className="flex h-full overflow-hidden bg-bg">
      <Sidebar
        sessionId={sessionId}
        onNewSession={() => {
          userInitiatedPane.current = false;
          lastPackageId.current = null;
          lastTutorId.current = null;
          lastAssessmentId.current = null;
        }}
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((o) => !o)}
      />

      <main className="flex-1 flex flex-col min-w-0">
        {/* Workspace header — editorial, restrained */}
        <header
          className="h-14 px-6 flex items-center justify-between shrink-0 animate-slide-down"
          style={{
            borderBottom: "1px solid rgb(var(--color-rule) / 0.6)",
            backgroundColor: "rgb(var(--color-bg-panel) / 0.4)",
          }}
        >
          <div className="flex items-baseline gap-3">
            <h1 className="font-display text-[15px] font-semibold tracking-tight">
              学习工作台
            </h1>
            <span className="text-[11px] text-fg-subtle font-mono-tab hidden sm:inline"
              style={{ letterSpacing: "0.08em" }}
            >
              Workspace
            </span>
          </div>
          <div className="flex items-center gap-3">
            <JobTray />
            {hasActiveJob && (
              <div className="text-[10px] flex items-center gap-1.5 px-2.5 h-7 rounded-full"
                style={{
                  color: "var(--color-brand-300)",
                  backgroundColor: "rgb(var(--color-brand-400) / 0.08)",
                  border: "1px solid rgb(var(--color-brand-500) / 0.25)",
                }}
              >
                <Activity className="w-3 h-3 animate-pulse" />
                <span className="font-medium">处理中</span>
              </div>
            )}
            <div
              className="hidden sm:inline-flex items-center gap-1.5 text-[10px] font-mono-tab text-fg-subtle px-2.5 h-7 rounded-full"
              style={{
                backgroundColor: "rgb(var(--color-bg-card) / 0.5)",
                border: "1px solid rgb(var(--color-rule) / 0.5)",
                letterSpacing: "0.08em",
              }}
            >
              <span>SESSION</span>
              <code style={{ color: "var(--color-brand-300)" }}>
                {sessionId ? sessionId.slice(0, 8) : "……"}
              </code>
            </div>
          </div>
        </header>

        {/* Center + right split */}
        <div className="flex-1 flex overflow-hidden">
          {/* Center: chat */}
          <section
            className="flex-1 flex flex-col min-w-0"
            style={{ borderRight: "1px solid rgb(var(--color-rule) / 0.6)" }}
          >
            <ChatMessages />
            <ChatComposer />
          </section>

          {/* Right: tabs */}
          <aside className="flex-1 bg-bg-subtle flex flex-col overflow-hidden min-w-[360px]">
            {/* Tab bar — editorial contents-page feel */}
            <div
              className="flex items-center gap-0.5 px-2 py-2 shrink-0 overflow-x-auto"
              style={{
                borderBottom: "1px solid rgb(var(--color-rule) / 0.6)",
                backgroundColor: "rgb(var(--color-bg-panel) / 0.4)",
              }}
            >
              <TabButton
                active={rightPane === "profile"}
                onClick={() => handleTabClick("profile")}
                icon={Brain}
                label="画像"
              />
              <TabButton
                active={rightPane === "path"}
                onClick={() => handleTabClick("path")}
                icon={TrendingUp}
                label="路径"
                badge={plannedPath ? "1" : undefined}
              />
              <TabButton
                active={rightPane === "resource"}
                onClick={() => handleTabClick("resource")}
                icon={Sparkles}
                label="资源"
                badge={
                  latestPackage ? String(latestPackage.resources.length) : undefined
                }
                accent="brand"
              />
              <TabButton
                active={rightPane === "tutor"}
                onClick={() => handleTabClick("tutor")}
                icon={MessageCircle}
                label="答疑"
                badge={latestTutorAnswer ? "1" : undefined}
                accent="teal"
              />
              <TabButton
                active={rightPane === "assessment"}
                onClick={() => handleTabClick("assessment")}
                icon={BarChart3}
                label="评估"
                badge={latestAssessment ? "1" : undefined}
                accent="green"
              />
            </div>

            {/* Pane body */}
            <div className="flex-1 overflow-hidden">
              {rightPane === "profile" && <ProfilePanel />}
              {rightPane === "path" && <PathPane />}
              {rightPane === "resource" && <ResourcePane />}
              {rightPane === "tutor" && <TutorPanel />}
              {rightPane === "assessment" && <AssessmentPanel />}
            </div>
          </aside>
        </div>
      </main>

      <SettingsModal />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab button
// ---------------------------------------------------------------------------

type TabAccent = "brand" | "teal" | "green";

const TAB_ACCENT: Record<TabAccent, { color: string; soft: string }> = {
  brand: { color: "var(--color-brand-400)", soft: "rgb(var(--color-brand-400) / 0.10)" },
  teal: { color: "var(--color-accent)", soft: "rgb(96 165 145 / 0.12)" },
  green: { color: "var(--color-accent-green)", soft: "rgb(124 168 110 / 0.12)" },
};

function TabButton({
  active,
  onClick,
  icon: Icon,
  label,
  badge,
  accent = "brand",
}: {
  active: boolean;
  onClick: () => void;
  icon: any;
  label: string;
  badge?: string;
  accent?: TabAccent;
}) {
  const a = TAB_ACCENT[accent];
  return (
    <button
      onClick={onClick}
      className={cn(
        "relative px-3 py-1.5 rounded-md text-[12.5px] flex items-center gap-1.5",
        "transition-all duration-150 shrink-0 font-medium",
      )}
      style={{
        color: active ? "var(--color-fg)" : "var(--color-fg-muted)",
        backgroundColor: active ? a.soft : "transparent",
      }}
      onMouseEnter={(e) => {
        if (!active) {
          e.currentTarget.style.color = "var(--color-fg)";
          e.currentTarget.style.backgroundColor = "rgb(var(--color-bg-card) / 0.5)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.color = "var(--color-fg-muted)";
          e.currentTarget.style.backgroundColor = "transparent";
        }
      }}
    >
      {active && (
        <span
          className="absolute left-2 right-2 -bottom-[5px] h-[2px] rounded-full"
          style={{ backgroundColor: a.color }}
        />
      )}
      <Icon
        className="w-3.5 h-3.5"
        style={{ color: active ? a.color : "var(--color-fg-subtle)" }}
      />
      {label}
      {badge && (
        <span
          className="ml-0.5 px-1.5 py-px rounded-full text-[9px] font-mono-tab"
          style={{
            backgroundColor: active ? a.color : "rgb(var(--color-bg-card))",
            color: active ? "rgb(20 14 8)" : "var(--color-fg-muted)",
            border: active ? "none" : "1px solid rgb(var(--color-rule))",
            letterSpacing: "0.05em",
            fontWeight: 600,
          }}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Path pane
// ---------------------------------------------------------------------------

function PathPane() {
  const plannedPath = useTutorStore((s) => s.plannedPath);
  const latestPackage = useTutorStore((s) => s.latestPackage);

  return (
    <div className="h-full overflow-y-auto">
      {plannedPath ? (
        <PathVisualizer path={plannedPath} />
      ) : (
        <div className="p-6 text-center text-fg-muted text-xs space-y-2">
          <Layers className="w-10 h-10 mx-auto opacity-30" />
          <p>暂无学习路径</p>
          <p className="text-fg-subtle leading-relaxed">
            完成一次资源生成后，系统会基于知识图谱为你规划路径
          </p>
        </div>
      )}
      {latestPackage && latestPackage.resources.length > 0 && (
        <div
          className="border-t"
          style={{ borderColor: "rgb(var(--color-rule) / 0.6)" }}
        >
          <ResourceTray />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Resource pane
// ---------------------------------------------------------------------------

function ResourcePane() {
  const latestPackage = useTutorStore((s) => s.latestPackage);
  const selectedResourceId = useTutorStore((s) => s.resourceSelection.selectedResourceId);
  const selectedResource = latestPackage?.resources.find(
    (r) => r.resource_id === selectedResourceId,
  );

  if (!latestPackage || latestPackage.resources.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-fg-muted text-center p-8">
        <Sparkles className="w-10 h-10 mx-auto mb-3 opacity-30" />
        <div className="text-sm font-display">暂无资源</div>
        <div className="text-[11px] text-fg-subtle mt-1">
          发送"系统学习 XXX"开始生成
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div
        className="max-h-[45%] overflow-y-auto shrink-0"
        style={{ borderBottom: "1px solid rgb(var(--color-rule) / 0.6)" }}
      >
        <ResourceTray />
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {selectedResource ? (
          <ResourceDetail resource={selectedResource} />
        ) : (
          <ResourceEmptyDetail />
        )}
      </div>
    </div>
  );
}
