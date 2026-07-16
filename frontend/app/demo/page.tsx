"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Download,
  FileDown,
  FlaskConical,
  Loader2,
  Play,
  RefreshCw,
  Route,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import {
  listDemoScenarios,
  loadDemoScenario,
  submitDemoCheckpoint,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import { useJobQueue } from "@/hooks/useJobQueue";
import type { ClientJob } from "@/lib/job-reducer";
import type {
  AgentTraceEvent,
  AssessmentReport,
  DemoCheckpoint,
  DemoCheckpointResult,
  DemoLoadResult,
  DemoScenario,
  LearnerProfileDetail,
  PlannedPath,
  Resource,
  ResourcePackage,
  StrategyDecision,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type DemoMode = "seeded" | "live";

const STATUS_STYLE: Record<string, string> = {
  queued: "border-fg/15 bg-bg-card text-fg-muted",
  running: "border-blue-400/30 bg-blue-500/10 text-blue-200",
  ready: "border-brand-400/30 bg-brand-500/10 text-brand-200",
  done: "border-green-400/30 bg-green-500/10 text-green-200",
  succeeded: "border-green-400/30 bg-green-500/10 text-green-200",
  warning: "border-yellow-400/30 bg-yellow-500/10 text-yellow-200",
  failed: "border-red-400/30 bg-red-500/10 text-red-200",
};

export default function DemoPage() {
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [demo, setDemo] = useState<DemoLoadResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMode, setLoadingMode] = useState<DemoMode | null>(null);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [checkpointResult, setCheckpointResult] = useState<DemoCheckpointResult | null>(null);
  const [submittingCheckpoint, setSubmittingCheckpoint] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reportRef = useRef<HTMLDivElement | null>(null);

  const userId = useTutorStore((s) => s.userId);
  const sessionId = useTutorStore((s) => s.sessionId);
  const setProfile = useTutorStore((s) => s.setProfile);
  const setLatestPackage = useTutorStore((s) => s.setLatestPackage);
  const setPlannedPath = useTutorStore((s) => s.setPlannedPath);
  const setLatestAssessment = useTutorStore((s) => s.setLatestAssessment);
  const setLatestStrategy = useTutorStore((s) => s.setLatestStrategy);
  const setSessionId = useTutorStore((s) => s.setSessionId);
  const liveJob = useTutorStore((s) =>
    demo?.live_job_id ? s.jobsById[demo.live_job_id] : undefined,
  );
  const jobQueue = useJobQueue(userId);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    listDemoScenarios()
      .then((res) => {
        if (!alive) return;
        const items = res.items ?? [];
        setScenarios(items);
        setSelectedId((current) => current || items[0]?.id || "");
      })
      .catch((e) => alive && setError(e?.message ?? String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const selectedScenario = useMemo(
    () => scenarios.find((item) => item.id === selectedId) ?? scenarios[0],
    [scenarios, selectedId],
  );

  const handleLoad = async (mode: DemoMode) => {
    if (!selectedScenario) return;
    setError(null);
    setLoadingMode(mode);
    setCheckpointResult(null);
    try {
      const result = await loadDemoScenario(selectedScenario.id, {
        user_id: userId || "competition-demo",
        session_id: sessionId || undefined,
        persist: true,
        mode,
      });
      setDemo(result);
      setProfile(result.profile as LearnerProfileDetail);
      setPlannedPath(result.path as PlannedPath);
      setLatestPackage(result.package as ResourcePackage);
      setLatestAssessment(result.assessment as AssessmentReport);
      setLatestStrategy(result.strategy as StrategyDecision);
      if (result.session_id) setSessionId(result.session_id);
      if (mode === "live" && result.live_job_id) {
        await jobQueue.refresh();
        jobQueue.subscribe(result.live_job_id, "resource_generation");
      }
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoadingMode(null);
    }
  };

  const handleCheckpoint = async (answer: string) => {
    if (!demo) return;
    setSubmittingCheckpoint(true);
    setError(null);
    try {
      const result = await submitDemoCheckpoint(demo.scenario.id, {
        user_id: demo.user_id,
        answer,
        elapsed_seconds: 20,
      });
      setCheckpointResult(result);
      setDemo((current) => {
        if (!current) return current;
        const nextProfile: LearnerProfileDetail = {
          ...current.profile,
          version: result.profile_version,
          knowledge_map: {
            ...current.profile.knowledge_map,
            [result.concept]: result.updated_mastery,
          },
          weak_concepts:
            result.updated_mastery >= 0.6
              ? current.profile.weak_concepts.filter(
                  (concept) => concept !== result.concept,
                )
              : current.profile.weak_concepts,
        };
        setProfile(nextProfile);
        return { ...current, profile: nextProfile };
      });
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setSubmittingCheckpoint(false);
    }
  };

  const handleMarkdownExport = () => {
    if (!demo) return;
    downloadText(
      `tutorbot-${demo.scenario.id}-demo-report.md`,
      buildMarkdownReport(demo),
      "text/markdown;charset=utf-8",
    );
  };

  const handlePdfExport = async () => {
    if (!reportRef.current || !demo) return;
    setExportingPdf(true);
    try {
      const [{ default: html2canvas }, { jsPDF }] = await Promise.all([
        import("html2canvas"),
        import("jspdf"),
      ]);
      const canvas = await html2canvas(reportRef.current, {
        backgroundColor: "#17130f",
        scale: 2,
        useCORS: true,
      });
      const imgData = canvas.toDataURL("image/png");
      const pdf = new jsPDF("p", "mm", "a4");
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const imgHeight = (canvas.height * pageWidth) / canvas.width;
      let heightLeft = imgHeight;
      let position = 0;

      pdf.addImage(imgData, "PNG", 0, position, pageWidth, imgHeight);
      heightLeft -= pageHeight;
      while (heightLeft > 0) {
        position = heightLeft - imgHeight;
        pdf.addPage();
        pdf.addImage(imgData, "PNG", 0, position, pageWidth, imgHeight);
        heightLeft -= pageHeight;
      }
      pdf.save(`tutorbot-${demo.scenario.id}-demo-report.pdf`);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setExportingPdf(false);
    }
  };

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-fg-muted">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        正在加载演示场景
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-bg text-fg">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-5 space-y-5">
        <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-2">
            <div className="inline-flex items-center gap-2 text-[11px] text-brand-200 border border-brand-500/25 bg-brand-500/10 rounded-md px-2 py-1">
              <FlaskConical className="w-3.5 h-3.5" />
              软件杯 A3 演示面板
            </div>
            <div>
              <h1 className="text-xl sm:text-2xl font-semibold">
                TutorBot 个性化学习闭环
              </h1>
              <p className="text-sm text-fg-muted mt-1 max-w-3xl">
                多智能体协同生成学生画像、学习路径、资源包、可信证据与下一步策略。
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <select
              className="input h-9 min-w-[220px]"
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              data-testid="demo-scenario-select"
            >
              {scenarios.map((scenario) => (
                <option key={scenario.id} value={scenario.id}>
                  {scenario.title}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-primary text-sm h-9"
              disabled={!selectedScenario || loadingMode !== null}
              onClick={() => handleLoad("seeded")}
              data-testid="demo-load-seeded"
            >
              {loadingMode === "seeded" ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              加载演示数据
            </button>
            <button
              type="button"
              className="btn-secondary text-sm h-9"
              disabled={!selectedScenario || loadingMode !== null}
              onClick={() => handleLoad("live")}
              data-testid="demo-load-live"
            >
              {loadingMode === "live" ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Sparkles className="w-4 h-4" />
              )}
              实时生成
            </button>
            <button
              type="button"
              className="btn-secondary text-sm h-9"
              disabled={!demo}
              onClick={handleMarkdownExport}
              data-testid="demo-export-markdown"
            >
              <Download className="w-4 h-4" />
              Markdown
            </button>
            <button
              type="button"
              className="btn-secondary text-sm h-9"
              disabled={!demo || exportingPdf}
              onClick={handlePdfExport}
              data-testid="demo-export-pdf"
            >
              {exportingPdf ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <FileDown className="w-4 h-4" />
              )}
              PDF
            </button>
          </div>
        </header>

        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        )}

        {!demo && selectedScenario && (
          <ScenarioPreview scenario={selectedScenario} />
        )}

        {demo && (
          <div ref={reportRef} className="space-y-5" data-testid="demo-report">
            {demo.mode === "live" && demo.live_job_id && (
              <LiveJobPanel
                jobId={demo.live_job_id}
                initialStatus={demo.live_job_status}
                job={liveJob}
              />
            )}
            <RuntimeWarnings warnings={demo.runtime_warnings} />
            <SummaryBand demo={demo} />

            <div className="grid grid-cols-1 xl:grid-cols-[1.25fr_0.75fr] gap-4">
              <Panel title="多智能体执行轨迹" icon={RefreshCw}>
                <AgentTimeline trace={demo.agent_trace} />
              </Panel>
              <Panel title="教师演示面板" icon={ShieldCheck}>
                <TeacherPanel data={demo.teacher_panel} />
              </Panel>
            </div>

            <Panel title="学习闭环" icon={Route}>
              <LearningLoop items={demo.learning_loop} />
            </Panel>

            {demo.checkpoint?.question && (
              <Panel title="闭环验证小测" icon={CheckCircle2}>
                <CheckpointPanel
                  checkpoint={demo.checkpoint}
                  result={checkpointResult}
                  submitting={submittingCheckpoint}
                  onAnswer={handleCheckpoint}
                />
              </Panel>
            )}

            <div className="grid grid-cols-1 xl:grid-cols-[0.9fr_1.1fr] gap-4">
              <Panel title="学生画像与路径" icon={UserRound}>
                <ProfileAndPath
                  profile={demo.profile}
                  path={demo.path}
                />
              </Panel>
              <Panel title="资源包与可信证据" icon={ShieldCheck}>
                <ResourceEvidenceGrid pkg={demo.package} />
              </Panel>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <Panel title="评测结果" icon={CheckCircle2}>
                <AssessmentPanel report={demo.assessment} />
              </Panel>
              <Panel title="下一步策略" icon={Sparkles}>
                <StrategyPanel strategy={demo.strategy} />
              </Panel>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function CheckpointPanel({
  checkpoint,
  result,
  submitting,
  onAnswer,
}: {
  checkpoint: DemoCheckpoint;
  result: DemoCheckpointResult | null;
  submitting: boolean;
  onAnswer: (answer: string) => void;
}) {
  return (
    <div className="space-y-3" data-testid="demo-checkpoint">
      <div>
        <div className="text-xs text-fg-subtle">{checkpoint.concept}</div>
        <p className="text-sm font-medium mt-1">{checkpoint.question}</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {checkpoint.options.map((option) => (
          <button
            key={option.value}
            type="button"
            className="rounded-md border border-fg/10 bg-bg-card px-3 py-2 text-left text-sm hover:border-brand-400/40 hover:bg-brand-500/5 disabled:opacity-60"
            disabled={submitting || result !== null}
            onClick={() => onAnswer(option.value)}
            data-testid={`demo-checkpoint-${option.value}`}
          >
            <span className="text-brand-300 mr-2">{option.value}</span>
            {option.label}
          </button>
        ))}
      </div>
      {submitting && (
        <div className="flex items-center gap-2 text-xs text-fg-muted">
          <Loader2 className="w-4 h-4 animate-spin" />
          正在写入学习事件并更新画像
        </div>
      )}
      {result && (
        <div
          className={cn(
            "rounded-md border px-3 py-2 text-sm",
            result.correct
              ? "border-green-400/30 bg-green-500/10 text-green-100"
              : "border-yellow-400/30 bg-yellow-500/10 text-yellow-100",
          )}
          data-testid="demo-checkpoint-result"
        >
          <div className="font-medium">
            {result.correct ? "回答正确，画像已更新" : "需要巩固，路径已调整"}
          </div>
          <div className="text-xs mt-1 opacity-90">
            {result.concept} 掌握度 {percent(result.previous_mastery)} → {percent(result.updated_mastery)} ·
            画像 v{result.profile_version} · 下一节点 {result.next_path_node}
          </div>
          <div className="text-xs mt-1 opacity-90">{result.recommendation}</div>
        </div>
      )}
    </div>
  );
}

const LIVE_STAGE_LABELS: Record<string, string> = {
  intent_understanding: "理解学习目标",
  profile_loading: "读取学生画像",
  knowledge_graph_query: "查询课程知识图谱",
  resource_planning: "规划个性化资源",
  content_and_pedagogy: "生成讲解与教学设计",
  parallel_resource_generation: "并行生成多模态资源",
  quality_review: "质量审核",
  anti_hallucination: "事实核查与安全过滤",
  package_assembly: "组装资源包",
  path_integration: "更新学习路径",
  persistence: "保存学习成果",
};

function LiveJobPanel({
  jobId,
  initialStatus,
  job,
}: {
  jobId: string;
  initialStatus: string;
  job?: ClientJob;
}) {
  const status = job?.status || initialStatus || "pending";
  const stageEvents = (job?.events || [])
    .filter((event) => event.type === "stage_start" || event.type === "stage_end")
    .slice(-8);
  const activeStage = job?.stage || "等待任务启动";
  const isTerminal = ["succeeded", "partial", "failed", "cancelled"].includes(status);

  return (
    <section
      className="rounded-lg border border-brand-400/30 bg-brand-500/10 px-4 py-3"
      data-testid="demo-live-job"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-brand-100">
            {isTerminal ? (
              <CheckCircle2 className="w-4 h-4" />
            ) : (
              <Loader2 className="w-4 h-4 animate-spin" />
            )}
            真实多智能体任务
          </div>
          <p className="text-xs text-fg-muted mt-1">
            任务 {jobId} · 状态 {status} · 当前阶段 {LIVE_STAGE_LABELS[activeStage] || activeStage}
          </p>
        </div>
        <div className="text-xs text-fg-muted">
          已接收 {job?.event_count || 0} 条真实流式事件
        </div>
      </div>
      {stageEvents.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {stageEvents.map((event, index) => (
            <span
              key={`${event.event_id || event.stage}-${index}`}
              className="rounded-md border border-fg/10 bg-bg-card px-2 py-1 text-[11px] text-fg-muted"
            >
              {event.type === "stage_end" ? "完成" : "开始"}：
              {LIVE_STAGE_LABELS[event.stage || ""] || event.stage || "未知阶段"}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function ScenarioPreview({ scenario }: { scenario: DemoScenario }) {
  return (
    <section className="rounded-xl border border-fg/10 bg-bg-panel p-5">
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-5">
        <div>
          <div className="text-[11px] text-fg-subtle">{scenario.course}</div>
          <h2 className="text-lg font-semibold mt-1">{scenario.title}</h2>
          <p className="text-sm text-fg-muted mt-2 leading-relaxed">
            {scenario.description}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4">
            <InfoBox label="学生画像" value={scenario.persona} />
            <InfoBox label="学习目标" value={scenario.goal} />
          </div>
        </div>
        <div className="rounded-lg border border-fg/10 bg-bg-card/50 p-4">
          <div className="text-[11px] text-fg-subtle">演示主题</div>
          <div className="text-base font-semibold mt-1">{scenario.topic}</div>
          <div className="flex items-center gap-2 text-xs text-fg-muted mt-3">
            <Clock3 className="w-4 h-4 text-brand-300" />
            约 {scenario.estimated_minutes} 分钟
          </div>
          <div className="flex flex-wrap gap-1.5 mt-4">
            {scenario.tags.map((tag) => (
              <span
                key={tag}
                className="px-2 py-1 rounded-md border border-fg/10 bg-bg-panel text-[11px] text-fg-muted"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function SummaryBand({ demo }: { demo: DemoLoadResult }) {
  const totalResources = demo.package.resources.length;
  const progress =
    demo.path.nodes.length > 0
      ? Math.round((demo.path.completed_count / demo.path.nodes.length) * 100)
      : 0;
  return (
    <section className="rounded-xl border border-fg/10 bg-bg-panel p-4">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Metric label="主题" value={demo.scenario.topic} />
        <Metric label="画像版本" value={`v${demo.profile.version}`} />
        <Metric label="路径进度" value={`${progress}%`} />
        <Metric label="资源数量" value={`${totalResources} 项`} />
        <Metric label="综合评测" value={percent(demo.assessment.overall_score)} />
      </div>
    </section>
  );
}

function RuntimeWarnings({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <div
      className="rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-100 space-y-1"
      data-testid="demo-runtime-warnings"
    >
      {warnings.map((warning, index) => (
        <div key={index} className="flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{warning}</span>
        </div>
      ))}
    </div>
  );
}

function Panel({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: any;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-fg/10 bg-bg-panel overflow-hidden">
      <header className="px-4 py-3 border-b border-fg/10 flex items-center gap-2">
        <Icon className="w-4 h-4 text-brand-300" />
        <h2 className="text-sm font-semibold">{title}</h2>
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

function AgentTimeline({ trace }: { trace: AgentTraceEvent[] }) {
  return (
    <ol className="space-y-3" data-testid="demo-agent-timeline">
      {trace.map((event, index) => (
        <li key={event.id} className="grid grid-cols-[24px_1fr] gap-3">
          <div className="flex flex-col items-center">
            <span className="w-6 h-6 rounded-full border border-brand-500/40 bg-brand-500/10 text-[11px] text-brand-100 flex items-center justify-center">
              {index + 1}
            </span>
            {index < trace.length - 1 && (
              <span className="w-px flex-1 min-h-10 bg-fg/10 mt-2" />
            )}
          </div>
          <div className="rounded-lg border border-fg/10 bg-bg-card/40 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold">{event.role}</span>
              <span className="text-[11px] text-fg-muted">{event.agent}</span>
              <StatusPill status={event.status} />
              <span className="text-[11px] text-fg-subtle ml-auto">
                {event.duration_ms} ms · {percent(event.confidence)}
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-3 text-xs">
              <TraceBox label="输入摘要" value={event.input_summary} />
              <TraceBox label="输出摘要" value={event.output_summary} />
            </div>
            {event.artifacts.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-3">
                {event.artifacts.map((artifact) => (
                  <code
                    key={artifact}
                    className="px-1.5 py-0.5 rounded bg-bg-panel border border-fg/10 text-[10px] text-fg-muted"
                  >
                    {artifact}
                  </code>
                ))}
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function LearningLoop({ items }: { items: Array<Record<string, unknown>> }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-2">
      {items.map((item, index) => (
        <div
          key={`${textOf(item.stage)}-${index}`}
          className="rounded-lg border border-fg/10 bg-bg-card/40 p-3 min-h-[132px]"
          data-testid={`demo-loop-${textOf(item.stage)}`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-fg-subtle">0{index + 1}</span>
            <StatusPill status={textOf(item.status)} />
          </div>
          <div className="font-semibold text-sm mt-3">{textOf(item.title)}</div>
          <p className="text-xs text-fg-muted leading-relaxed mt-2">
            {textOf(item.summary)}
          </p>
        </div>
      ))}
    </div>
  );
}

function ProfileAndPath({
  profile,
  path,
}: {
  profile: LearnerProfileDetail;
  path: PlannedPath;
}) {
  const knowledgeEntries = Object.entries(profile.knowledge_map || {}).sort(
    (a, b) => a[1] - b[1],
  );
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <Metric label="认知风格" value={profile.cognitive_style} compact />
        <Metric label="主偏好" value={profile.modality_dominant} compact />
        <Metric label="弱点数" value={String(profile.weak_concepts.length)} compact />
        <Metric label="自我效能" value={percent(profile.self_efficacy)} compact />
      </div>

      <div>
        <SectionTitle>知识掌握</SectionTitle>
        <div className="space-y-2 mt-2">
          {knowledgeEntries.map(([concept, score]) => (
            <ProgressRow key={concept} label={concept} value={score} />
          ))}
        </div>
      </div>

      <div>
        <SectionTitle>路径节点</SectionTitle>
        <div className="space-y-2 mt-2">
          {path.nodes.map((node) => (
            <div
              key={node.id}
              className="rounded-lg border border-fg/10 bg-bg-card/40 px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{node.name}</span>
                <StatusPill status={node.status} />
              </div>
              <div className="text-[11px] text-fg-muted mt-1">
                难度 {node.difficulty} · {node.estimated_hours} 小时
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ResourceEvidenceGrid({ pkg }: { pkg: ResourcePackage }) {
  return (
    <div className="space-y-3" data-testid="demo-resource-evidence">
      <div className="flex flex-wrap gap-2">
        {pkg.generated_by.map((agent) => (
          <span
            key={agent}
            className="px-2 py-1 rounded-md border border-fg/10 bg-bg-card text-[11px] text-fg-muted"
          >
            {agent}
          </span>
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {pkg.resources.map((resource) => (
          <ResourceEvidenceCard key={resource.resource_id} resource={resource} />
        ))}
      </div>
    </div>
  );
}

function ResourceEvidenceCard({ resource }: { resource: Resource }) {
  const review = recordOf(resource.review);
  const safety = recordOf(resource.safety);
  const citations = resource.citations ?? [];
  return (
    <article className="rounded-lg border border-fg/10 bg-bg-card/40 p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[11px] text-fg-subtle">{resource.type}</div>
          <h3 className="text-sm font-semibold mt-0.5">{resource.title}</h3>
        </div>
        <span className="text-xs text-brand-200">
          {percent(resource.confidence_score)}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 mt-3 text-[11px]">
        <EvidenceMetric
          label="质量"
          value={`${textOf(review.verdict || "n/a")} · ${percent(numberOf(review.quality_score))}`}
        />
        <EvidenceMetric
          label="安全"
          value={`${textOf(safety.verdict || "n/a")} · ${textOf(safety.risk_level || "n/a")}`}
        />
      </div>
      {citations.length > 0 && (
        <div className="mt-3">
          <SectionTitle small>引用</SectionTitle>
          <ul className="space-y-1 mt-1">
            {citations.slice(0, 3).map((citation, index) => {
              const c = recordOf(citation);
              return (
                <li key={index} className="text-[11px] text-fg-muted truncate">
                  {textOf(c.title || c.url || c.source)}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {(resource.unverified_claims ?? []).length > 0 && (
        <div className="mt-3 rounded-md border border-yellow-500/25 bg-yellow-500/10 p-2 text-[11px] text-yellow-100">
          {resource.unverified_claims?.join("；")}
        </div>
      )}
    </article>
  );
}

function TeacherPanel({ data }: { data: Record<string, unknown> }) {
  const weak = arrayOf(data.weak_concepts);
  const interventions = arrayOf(data.interventions);
  const evidence = arrayOf(data.evidence);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <Metric label="进度" value={`${numberOf(data.progress_pct)}%`} compact />
        <Metric label="风险" value={textOf(data.risk_level)} compact />
      </div>
      <InfoBox label="学生" value={textOf(data.class_snapshot)} />
      <TagGroup label="薄弱点" items={weak} />
      <ListBlock title="推荐干预" items={interventions} />
      <ListBlock title="证据" items={evidence} />
    </div>
  );
}

function AssessmentPanel({ report }: { report: AssessmentReport }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <Metric label="综合得分" value={percent(report.overall_score)} compact />
        <Metric label="趋势" value={report.trajectory} compact />
      </div>
      <div className="space-y-2">
        {Object.values(report.dimension_scores || {}).map((score) => (
          <ProgressRow
            key={score.dimension}
            label={score.dimension}
            value={score.score}
            note={score.notes}
          />
        ))}
      </div>
      <ListBlock title="建议" items={report.recommendations} />
    </div>
  );
}

function StrategyPanel({ strategy }: { strategy: StrategyDecision }) {
  return (
    <div className="space-y-4">
      <InfoBox label="总策略" value={strategy.overall_directive} />
      <div className="space-y-2">
        {strategy.actions.map((action, index) => (
          <div
            key={`${action.action_type}-${index}`}
            className="rounded-lg border border-fg/10 bg-bg-card/40 p-3"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold">
                {action.target_concept}
              </span>
              <span className="text-[11px] text-brand-200">
                P{action.priority}
              </span>
            </div>
            <div className="text-[11px] text-fg-muted mt-1">
              {action.action_type} · {action.target_resource_type}
            </div>
            <p className="text-xs text-fg-muted leading-relaxed mt-2">
              {action.rationale}
            </p>
          </div>
        ))}
      </div>
      {strategy.notes && <InfoBox label="备注" value={strategy.notes} />}
    </div>
  );
}

function Metric({
  label,
  value,
  compact,
}: {
  label: string;
  value: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-fg/10 bg-bg-card/40",
        compact ? "p-3" : "p-4 min-h-[80px]",
      )}
    >
      <div className="text-[11px] text-fg-subtle">{label}</div>
      <div
        className={cn(
          "font-semibold mt-1 break-words",
          compact ? "text-sm" : "text-base",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function InfoBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-fg/10 bg-bg-card/40 p-3">
      <div className="text-[11px] text-fg-subtle">{label}</div>
      <div className="text-sm text-fg-muted leading-relaxed mt-1">{value}</div>
    </div>
  );
}

function TraceBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-fg/10 bg-bg-panel/50 p-2">
      <div className="text-[10px] text-fg-subtle mb-1">{label}</div>
      <div className="text-fg-muted leading-relaxed">{value}</div>
    </div>
  );
}

function EvidenceMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-fg/10 bg-bg-panel/50 p-2">
      <span className="text-fg-subtle">{label}</span>
      <span className="block text-fg-muted mt-0.5">{value}</span>
    </div>
  );
}

function ProgressRow({
  label,
  value,
  note,
}: {
  label: string;
  value: number;
  note?: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-fg-muted">{label}</span>
        <span className="text-fg-subtle">{percent(value)}</span>
      </div>
      <div className="h-1.5 rounded-full bg-bg-card overflow-hidden mt-1">
        <div
          className="h-full rounded-full bg-brand-400"
          style={{ width: `${Math.max(0, Math.min(100, Math.round(value * 100)))}%` }}
        />
      </div>
      {note && <div className="text-[11px] text-fg-subtle mt-1">{note}</div>}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px]",
        STATUS_STYLE[status] ?? STATUS_STYLE.queued,
      )}
    >
      {status}
    </span>
  );
}

function SectionTitle({
  children,
  small,
}: {
  children: React.ReactNode;
  small?: boolean;
}) {
  return (
    <div className={cn("font-semibold text-fg", small ? "text-[11px]" : "text-xs")}>
      {children}
    </div>
  );
}

function TagGroup({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <SectionTitle>{label}</SectionTitle>
      <div className="flex flex-wrap gap-1.5 mt-2">
        {items.map((item) => (
          <span
            key={item}
            className="px-2 py-1 rounded-md border border-fg/10 bg-bg-card text-[11px] text-fg-muted"
          >
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}

function ListBlock({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <SectionTitle>{title}</SectionTitle>
      <ul className="space-y-1.5 mt-2">
        {items.map((item, index) => (
          <li
            key={`${item}-${index}`}
            className="text-xs text-fg-muted leading-relaxed rounded-md border border-fg/10 bg-bg-card/40 px-2 py-1.5"
          >
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function buildMarkdownReport(demo: DemoLoadResult): string {
  const resources = demo.package.resources
    .map((resource) => {
      const review = recordOf(resource.review);
      const citations = (resource.citations ?? [])
        .map((citation) => `  - ${textOf(recordOf(citation).title || recordOf(citation).url)}`)
        .join("\n");
      return [
        `### ${resource.title}`,
        `- 类型：${resource.type}`,
        `- 置信度：${percent(resource.confidence_score)}`,
        `- 生成 Agent：${resource.generated_by.join(", ") || "n/a"}`,
        `- 审核：${textOf(review.verdict || "n/a")} / ${percent(numberOf(review.quality_score))}`,
        resource.unverified_claims?.length
          ? `- 待核验声明：${resource.unverified_claims.join("；")}`
          : "- 待核验声明：无",
        citations ? `- 引用：\n${citations}` : "- 引用：无",
      ].join("\n");
    })
    .join("\n\n");

  const agentTrace = demo.agent_trace
    .map(
      (event, index) =>
        `${index + 1}. ${event.role}（${event.agent}）：${event.output_summary} ` +
        `[${event.status}, ${event.duration_ms}ms, confidence=${percent(event.confidence)}]`,
    )
    .join("\n");

  return [
    `# TutorBot 比赛演示报告`,
    "",
    `## 场景`,
    `- 标题：${demo.scenario.title}`,
    `- 主题：${demo.scenario.topic}`,
    `- 学生：${demo.scenario.persona}`,
    `- 目标：${demo.scenario.goal}`,
    "",
    `## 学生画像`,
    `- 认知风格：${demo.profile.cognitive_style}`,
    `- 平均掌握度：${percent(demo.profile.avg_mastery)}`,
    `- 薄弱概念：${demo.profile.weak_concepts.join(", ") || "无"}`,
    `- 优势概念：${demo.profile.strong_concepts.join(", ") || "无"}`,
    "",
    `## 学习路径`,
    ...demo.path.nodes.map(
      (node) => `- ${node.name}：${node.status}，预计 ${node.estimated_hours} 小时`,
    ),
    "",
    `## 多智能体轨迹`,
    agentTrace,
    "",
    `## 资源与证据`,
    resources,
    "",
    `## 测评结果`,
    `- 综合得分：${percent(demo.assessment.overall_score)}`,
    `- 趋势：${demo.assessment.trajectory}`,
    `- 建议：${demo.assessment.recommendations.join("；")}`,
    "",
    `## 下一步建议`,
    `- ${demo.strategy.overall_directive}`,
    ...demo.strategy.actions.map((action) => `- ${action.target_concept}：${action.rationale}`),
    "",
    `## 运行提示`,
    ...(demo.runtime_warnings.length
      ? demo.runtime_warnings.map((warning) => `- ${warning}`)
      : ["- 无"]),
  ].join("\n");
}

function downloadText(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function percent(value: number | undefined | null): string {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function textOf(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function numberOf(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function arrayOf(value: unknown): string[] {
  return Array.isArray(value) ? value.map(textOf).filter(Boolean) : [];
}
