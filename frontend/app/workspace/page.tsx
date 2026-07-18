"use client";

import { useEffect, useRef, useState } from "react";
import {
  BookOpenCheck,
  ClipboardCheck,
  Files,
  List,
  Map,
  MessageCircle,
  PanelRightOpen,
  X,
} from "lucide-react";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatMessages } from "@/components/chat/ChatMessages";
import { JobTray } from "@/components/chat/JobTray";
import { ProfilePanel } from "@/components/profile/ProfilePanel";
import { ResourceTray } from "@/components/resources/ResourceTray";
import { ResourceDetail, ResourceEmptyDetail } from "@/components/resources/ResourceCard";
import { PathVisualizer } from "@/components/kg/PathVisualizer";
import { TutorPanel } from "@/components/tutor/TutorPanel";
import { AssessmentPanel } from "@/components/assessment/AssessmentPanel";
import { CourseTaskWorkbench } from "@/components/workspace/CourseTaskWorkbench";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTutorStore } from "@/lib/store";
import { refreshLearningState } from "@/lib/learning-state";
import { cn } from "@/lib/utils";

type DetailPane = "status" | "path" | "resource" | "explanation" | "review";

export default function WorkspacePage() {
  const sessionId = useTutorStore((state) => state.sessionId);
  const currentCourse = useTutorStore((state) => state.currentCourse);
  const hydrateSessionId = useTutorStore((state) => state.hydrateSessionId);
  const setSessionId = useTutorStore((state) => state.setSessionId);
  const resetSession = useTutorStore((state) => state.resetSession);
  const loadConversationAggregate = useTutorStore((state) => state.loadConversationAggregate);
  const userId = useTutorStore((state) => state.userId);
  const messagesLength = useTutorStore((state) => state.messages.length);
  const latestPackage = useTutorStore((state) => state.latestPackage);
  const latestTutorAnswer = useTutorStore((state) => state.latestTutorAnswer);
  const latestAssessment = useTutorStore((state) => state.latestAssessment);
  const selectedResourceId = useTutorStore((state) => state.resourceSelection.selectedResourceId);
  const plannedPath = useTutorStore((state) => state.plannedPath);
  const jobsById = useTutorStore((state) => state.jobsById);
  const jobOrder = useTutorStore((state) => state.jobOrder);

  const [showOverview, setShowOverview] = useState(true);
  const [taskCreationOpen, setTaskCreationOpen] = useState(false);
  const [detailPane, setDetailPane] = useState<DetailPane>("status");
  const [detailOpen, setDetailOpen] = useState(false);
  const userSelectedPane = useRef(false);
  const restoreAttempted = useRef(false);
  const lastPackageId = useRef<string | null>(null);
  const lastTutorId = useRef<string | null>(null);
  const lastAssessmentId = useRef<string | null>(null);
  const lastTerminalSignature = useRef("");

  useEffect(() => { hydrateSessionId(); }, [hydrateSessionId]);

  useEffect(() => {
    if (restoreAttempted.current || !sessionId || !userId || messagesLength > 0) return;
    let stored: string | null = null;
    try { stored = window.localStorage.getItem("tutor:lastSessionId"); } catch { /* storage unavailable */ }
    restoreAttempted.current = true;
    if (stored !== sessionId) return;
    void loadConversationAggregate(userId, sessionId).catch(() => undefined);
  }, [loadConversationAggregate, messagesLength, sessionId, setSessionId, userId]);

  useEffect(() => { if (messagesLength > 0) setShowOverview(false); }, [messagesLength]);
  useWebSocket();

  useEffect(() => {
    const signature = jobOrder
      .map((jobId) => `${jobId}:${jobsById[jobId]?.status || ""}`)
      .join("|");
    const hasNewTerminal = jobOrder.some((jobId) =>
      ["succeeded", "partial", "failed", "cancelled"].includes(jobsById[jobId]?.status),
    );
    if (!hasNewTerminal || signature === lastTerminalSignature.current) return;
    lastTerminalSignature.current = signature;
    void refreshLearningState(userId, currentCourse).catch(() => undefined);
  }, [currentCourse, jobOrder, jobsById, userId]);

  useEffect(() => {
    void refreshLearningState(userId, currentCourse).catch(() => undefined);
  }, [currentCourse, userId]);

  useEffect(() => {
    if (!latestPackage || lastPackageId.current === latestPackage.package_id) return;
    lastPackageId.current = latestPackage.package_id;
    if (latestPackage.resources.length && !userSelectedPane.current) {
      setDetailPane("resource"); setDetailOpen(true);
    }
  }, [latestPackage]);

  useEffect(() => {
    if (!latestTutorAnswer) return;
    const key = latestTutorAnswer.full_markdown?.slice(0, 32) || "answer";
    if (lastTutorId.current === key) return;
    lastTutorId.current = key;
    if (!userSelectedPane.current) { setDetailPane("explanation"); setDetailOpen(true); }
  }, [latestTutorAnswer]);

  useEffect(() => {
    if (!latestAssessment) return;
    const key = latestAssessment.created_at || "review";
    if (lastAssessmentId.current === key) return;
    lastAssessmentId.current = key;
    if (!userSelectedPane.current) { setDetailPane("review"); setDetailOpen(true); }
  }, [latestAssessment]);

  useEffect(() => { if (selectedResourceId) { setDetailPane("resource"); setDetailOpen(true); } }, [selectedResourceId]);

  const activeJobs = Object.values(jobsById).filter((job) => !["succeeded", "partial", "failed", "cancelled"].includes(job.status)).length;

  const startNewTask = () => {
    setSessionId(window.crypto.randomUUID());
    resetSession();
    userSelectedPane.current = false;
    setTaskCreationOpen(true);
    setShowOverview(false);
    setDetailOpen(false);
  };

  const choosePane = (pane: DetailPane) => {
    userSelectedPane.current = true;
    setDetailPane(pane);
    setDetailOpen(true);
  };

  return (
    <div className="flex h-full overflow-hidden bg-bg-panel">
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-16 shrink-0 items-center justify-between gap-4 border-b border-border bg-bg-panel px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" onClick={() => { setShowOverview(true); setTaskCreationOpen(false); }} className="flex min-h-11 items-center gap-2 rounded-full px-3 text-sm font-semibold text-fg-muted hover:bg-bg-subtle hover:text-fg">
              <List className="h-4 w-4" />
              <span className="hidden sm:inline">学习任务</span>
            </button>
            {!showOverview && <><span className="text-fg-subtle">/</span><span className="truncate text-sm font-semibold">{currentCourse === "ai_introduction" ? "人工智能导论" : currentCourse}</span></>}
          </div>
          <div className="flex items-center gap-2">
            <JobTray />
            {activeJobs > 0 && <span className="hidden text-xs text-fg-muted sm:inline">{activeJobs} 项正在准备</span>}
            {!showOverview && (
              <button type="button" onClick={() => setDetailOpen((open) => !open)} className="flex min-h-11 items-center gap-2 rounded-full px-3 text-sm font-semibold text-fg-muted hover:bg-bg-subtle hover:text-fg" aria-expanded={detailOpen}>
                <PanelRightOpen className="h-4 w-4" />
                <span className="hidden sm:inline">学习详情</span>
              </button>
            )}
          </div>
        </header>

        <div className="relative flex min-h-0 flex-1 overflow-hidden">
          <section className="flex min-w-0 flex-1 flex-col bg-bg-panel">
            {showOverview ? (
              <div className="h-full overflow-y-auto">
                <CourseTaskWorkbench
                  creating={taskCreationOpen}
                  onCreateTask={startNewTask}
                  onTaskSelected={(hasContent) => {
                    setTaskCreationOpen(!hasContent);
                    setShowOverview(false);
                  }}
                />
              </div>
            ) : (
              <>
                <ChatMessages />
                <ChatComposer mode={messagesLength > 0 ? "continue" : "create"} autoFocus={taskCreationOpen && messagesLength === 0} />
              </>
            )}
          </section>

          {detailOpen && !showOverview && (
            <aside className="absolute inset-y-0 right-0 z-30 flex w-[min(520px,96vw)] shrink-0 flex-col overflow-hidden border-l border-border bg-bg-panel xl:relative" aria-label="学习详情">
              <div className="flex min-h-14 shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-2">
                <DetailTab active={detailPane === "status"} onClick={() => choosePane("status")} icon={BookOpenCheck} label="学习状态" />
                <DetailTab active={detailPane === "path"} onClick={() => choosePane("path")} icon={Map} label="下一步" badge={plannedPath ? "" : undefined} />
                <DetailTab active={detailPane === "resource"} onClick={() => choosePane("resource")} icon={Files} label="资料" badge={latestPackage ? String(latestPackage.resources.length) : undefined} />
                <DetailTab active={detailPane === "explanation"} onClick={() => choosePane("explanation")} icon={MessageCircle} label="讲解" />
                <DetailTab active={detailPane === "review"} onClick={() => choosePane("review")} icon={ClipboardCheck} label="练习回顾" />
                <button type="button" onClick={() => setDetailOpen(false)} className="ml-auto flex min-h-10 min-w-10 items-center justify-center rounded-full text-fg-muted hover:bg-bg-subtle hover:text-fg" aria-label="关闭学习详情"><X className="h-4 w-4" /></button>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                {detailPane === "status" && <ProfilePanel />}
                {detailPane === "path" && <PathPane />}
                {detailPane === "resource" && <ResourcePane />}
                {detailPane === "explanation" && <TutorPanel />}
                {detailPane === "review" && <AssessmentPanel />}
              </div>
            </aside>
          )}
        </div>
      </main>
    </div>
  );
}

function DetailTab({ active, onClick, icon: Icon, label, badge }: { active: boolean; onClick: () => void; icon: typeof Files; label: string; badge?: string }) {
  return <button type="button" onClick={onClick} className={cn("flex min-h-10 shrink-0 items-center gap-1.5 rounded-full px-3 text-xs font-semibold transition-colors", active ? "bg-bg-subtle text-fg" : "text-fg-muted hover:bg-bg-subtle/70 hover:text-fg")}><Icon className="h-3.5 w-3.5" />{label}{badge && <span className="text-[10px] text-fg-subtle">{badge}</span>}</button>;
}

function PathPane() {
  const plannedPath = useTutorStore((state) => state.plannedPath);
  return <div className="h-full overflow-y-auto">{plannedPath ? <PathVisualizer path={plannedPath} /> : <div className="flex min-h-full flex-col items-center justify-center p-8 text-center"><Map className="h-8 w-8 text-fg-muted" /><p className="mt-4 text-sm font-semibold">还没有安排下一步</p><p className="mt-2 max-w-xs text-xs leading-5 text-fg-muted">告诉 TutorBot 你的目标和当前基础，它会把后续内容整理成清晰顺序。</p></div>}</div>;
}

function ResourcePane() {
  const latestPackage = useTutorStore((state) => state.latestPackage);
  const selectedResourceId = useTutorStore((state) => state.resourceSelection.selectedResourceId);
  const selected = latestPackage?.resources.find((resource) => resource.resource_id === selectedResourceId);
  if (!latestPackage?.resources.length) return <div className="flex h-full flex-col items-center justify-center p-8 text-center"><Files className="h-8 w-8 text-fg-muted" /><p className="mt-4 text-sm font-semibold">还没有学习资料</p><p className="mt-2 text-xs text-fg-muted">选择“整理学习资料”，完成后会出现在这里。</p></div>;
  return <div className="flex h-full flex-col"><div className="max-h-[38%] shrink-0 overflow-y-auto border-b border-border"><ResourceTray /></div><div className="min-h-0 flex-1 overflow-hidden">{selected ? <ResourceDetail resource={selected} /> : <ResourceEmptyDetail />}</div></div>;
}
