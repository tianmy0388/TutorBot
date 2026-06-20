"use client";

/**
 * HomePage — main 3-column layout:
 *  - Sidebar (left): sessions, capabilities, courses
 *  - Center column: chat stream + composer
 *  - Right column: profile / resource tray / KG path / tutor / assessment
 *
 * Tabs in the right pane:
 *  - 画像    : learner profile (6 dimensions, knowledge map, error patterns)
 *  - 路径    : planned learning path from KG
 *  - 资源    : latest resource package (tray + detail)
 *  - 答疑    : latest tutoring result (4-layer answer + enrichments)
 *  - 评估    : latest assessment + adaptive strategy
 *
 * Auto-switch logic:
 *  - When a new resource package arrives → resource tab
 *  - When a new tutoring result arrives → tutor tab
 *  - When a new assessment arrives     → assessment tab
 *  - A resource being selected         → resource tab
 *
 * The WebSocket connection is kept alive by `useWebSocket()` so that
 * streaming events flow into the store regardless of which component
 * initiated the turn.
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
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTutorStore } from "@/lib/store";
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
  // ``sessionId`` is generated client-side only — ``crypto.randomUUID()``
  // runs on the server during SSR and again on the client during hydration
  // and produces different values, causing a React hydration mismatch.
  // Defer the generation to ``useEffect`` (post-mount) so the SSR output
  // is stable; the client takes over after hydration.
  const [sessionId, setSessionId] = useState<string>("");
  useEffect(() => {
    setSessionId(crypto.randomUUID());
  }, []);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [rightPane, setRightPane] = useState<RightPane>("profile");
  const userInitiatedPane = useRef(false);

  // Keep the WebSocket connection alive for the whole page lifetime
  useWebSocket();

  const latestPackage = useTutorStore((s) => s.latestPackage);
  const latestTutorAnswer = useTutorStore((s) => s.latestTutorAnswer);
  const latestAssessment = useTutorStore((s) => s.latestAssessment);
  const selectedResourceId = useTutorStore((s) => s.resourceSelection.selectedResourceId);
  const plannedPath = useTutorStore((s) => s.plannedPath);
  const activeTurnPhase = useTutorStore((s) => s.activeTurn.phase);

  // Track whether the user manually clicked a tab; if so, stop auto-switching
  // until a *new* result of a different kind arrives.
  const lastPackageId = useRef<string | null>(null);
  const lastTutorId = useRef<string | null>(null);
  const lastAssessmentId = useRef<string | null>(null);

  // Auto-switch on new package
  useEffect(() => {
    if (!latestPackage) return;
    if (lastPackageId.current === latestPackage.package_id) return;
    lastPackageId.current = latestPackage.package_id;
    if (latestPackage.resources.length > 0 && !userInitiatedPane.current) {
      setRightPane("resource");
    }
  }, [latestPackage]);

  // Auto-switch on new tutor answer
  useEffect(() => {
    if (!latestTutorAnswer) return;
    // Identify by full_markdown hash-ish to detect "new"
    const id = latestTutorAnswer.full_markdown?.slice(0, 32) || "x";
    if (lastTutorId.current === id) return;
    lastTutorId.current = id;
    if (!userInitiatedPane.current) {
      setRightPane("tutor");
    }
  }, [latestTutorAnswer]);

  // Auto-switch on new assessment
  useEffect(() => {
    if (!latestAssessment) return;
    const id = latestAssessment.created_at || "x";
    if (lastAssessmentId.current === id) return;
    lastAssessmentId.current = id;
    if (!userInitiatedPane.current) {
      setRightPane("assessment");
    }
  }, [latestAssessment]);

  // When a resource is selected, switch to detail pane
  useEffect(() => {
    if (selectedResourceId) {
      setRightPane("resource");
    }
  }, [selectedResourceId]);

  // Find the selected resource object
  const selectedResource = latestPackage?.resources.find(
    (r) => r.resource_id === selectedResourceId,
  );

  const handleTabClick = (pane: RightPane) => {
    userInitiatedPane.current = true;
    setRightPane(pane);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-bg">
      <Sidebar
        sessionId={sessionId}
        onNewSession={() => {
          userInitiatedPane.current = false;
          lastPackageId.current = null;
          lastTutorId.current = null;
          lastAssessmentId.current = null;
          setSessionId(crypto.randomUUID());
        }}
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((o) => !o)}
      />

      <main className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="border-b border-fg/10 px-6 py-3 flex items-center justify-between bg-bg-panel/50 backdrop-blur shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-brand-500 via-brand-600 to-accent flex items-center justify-center text-white font-bold shadow-md">
              T
            </div>
            <div>
              <h1 className="font-semibold text-sm">Tutor</h1>
              <p className="text-[10px] text-fg-muted">
                多智能体个性化学习资源生成系统
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <JobTray />
            {activeTurnPhase !== "idle" && (
              <div className="text-[10px] text-brand-300 flex items-center gap-1">
                <Activity className="w-3 h-3 animate-pulse" />
                处理中…
              </div>
            )}
            <div className="text-[10px] text-fg-muted hidden sm:block">
              Session:{" "}
              <code className="text-accent font-mono">
                {sessionId ? sessionId.slice(0, 8) : "……"}
              </code>
            </div>
          </div>
        </header>

        {/* Center + right split */}
        <div className="flex-1 flex overflow-hidden">
          {/* Center: chat */}
          <section className="flex-1 flex flex-col min-w-0 border-r border-fg/10">
            <ChatMessages />
            <ChatComposer />
          </section>

          {/* Right: tabs */}
          <aside className="w-[420px] bg-bg-panel flex flex-col overflow-hidden">
            {/* Tab bar */}
            <div className="flex items-center gap-1 px-2 py-2 border-b border-fg/10 shrink-0 bg-bg-panel/80 overflow-x-auto">
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
              />
              <TabButton
                active={rightPane === "tutor"}
                onClick={() => handleTabClick("tutor")}
                icon={MessageCircle}
                label="答疑"
                badge={latestTutorAnswer ? "1" : undefined}
                accent="brand"
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab button
// ---------------------------------------------------------------------------

function TabButton({
  active,
  onClick,
  icon: Icon,
  label,
  badge,
  accent,
}: {
  active: boolean;
  onClick: () => void;
  icon: any;
  label: string;
  badge?: string;
  accent?: "brand" | "green";
}) {
  const accentClass = accent === "brand"
    ? "bg-brand-600/30 text-brand-200"
    : accent === "green"
    ? "bg-green-700/30 text-green-200"
    : "bg-brand-600/30 text-brand-200";

  return (
    <button
      onClick={onClick}
      className={cn(
        "px-2.5 py-1.5 rounded-md text-xs flex items-center gap-1.5 transition-colors relative shrink-0",
        active
          ? accentClass
          : "text-fg-muted hover:text-fg hover:bg-bg-card",
      )}
    >
      <Icon className="w-3.5 h-3.5" />
      {label}
      {badge && (
        <span
          className={cn(
            "ml-1 px-1.5 py-0 rounded-full text-[9px] font-mono",
            active
              ? "bg-brand-500 text-white"
              : "bg-bg-card text-fg-muted border border-fg/10",
          )}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Path pane — shows the planned learning path + resource list below
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
        <div className="border-t border-fg/10">
          <ResourceTray />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Resource pane — split: tray on top, detail on bottom
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
        <div className="text-sm">暂无资源</div>
        <div className="text-[11px] text-fg-subtle mt-1">
          发送"系统学习 XXX"开始生成
        </div>
      </div>
    );
  }

  // Split layout: tray + detail
  return (
    <div className="h-full flex flex-col">
      <div className="max-h-[45%] overflow-y-auto border-b border-fg/10 shrink-0">
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