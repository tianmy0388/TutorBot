"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Database,
  Loader2,
  Plus,
  RefreshCw,
  Route,
  UserRound,
} from "lucide-react";
import {
  listAppCourses,
  listConversations,
  listKnowledgeBases,
  type ConversationSummary,
  type CourseResponse,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import type { KnowledgeBaseSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

interface CourseTaskWorkbenchProps {
  creating: boolean;
  onCreateTask: () => void;
  onTaskSelected: (hasContent: boolean) => void;
}

type ContextSummary = {
  title: string;
  detail: string;
};

export function CourseTaskWorkbench({
  creating,
  onCreateTask,
  onTaskSelected,
}: CourseTaskWorkbenchProps) {
  const userId = useTutorStore((state) => state.userId);
  const sessionId = useTutorStore((state) => state.sessionId);
  const currentCourse = useTutorStore((state) => state.currentCourse);
  const plannedPath = useTutorStore((state) => state.plannedPath);
  const ragEnabled = useTutorStore((state) => state.ragEnabled);
  const retrievalScope = useTutorStore((state) => state.retrievalScope);
  const setSessionId = useTutorStore((state) => state.setSessionId);
  const loadConversationAggregate = useTutorStore(
    (state) => state.loadConversationAggregate,
  );
  const profile = useTutorStore((state) => state.profile);

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [courses, setCourses] = useState<CourseResponse[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [courseState, setCourseState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [knowledgeState, setKnowledgeState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [openingTaskId, setOpeningTaskId] = useState<string | null>(null);

  const loadWorkbench = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setTaskError(null);

    const [conversationResult, courseResult, knowledgeResult] =
      await Promise.allSettled([
        listConversations(userId, { limit: 50 }),
        listAppCourses(),
        listKnowledgeBases(),
      ]);

    if (conversationResult.status === "fulfilled") {
      setConversations(conversationResult.value.items || []);
    } else {
      setTaskError("任务记录暂时无法读取，请稍后重试。");
    }

    if (courseResult.status === "fulfilled") {
      setCourses(courseResult.value.items || []);
      setCourseState("ready");
    } else {
      setCourseState("error");
    }

    if (knowledgeResult.status === "fulfilled") {
      setKnowledgeBases(knowledgeResult.value.items || []);
      setKnowledgeState("ready");
    } else {
      setKnowledgeState("error");
    }

    setLoading(false);
  }, [userId]);

  useEffect(() => {
    void loadWorkbench();
  }, [loadWorkbench]);

  const course = useMemo(
    () =>
      courses.find(
        (item) =>
          item.id === currentCourse || item.knowledge_graph_id === currentCourse,
      ),
    [courses, currentCourse],
  );

  const courseName =
    course?.name ||
    (currentCourse === "ai_introduction" ? "人工智能导论" : currentCourse);

  const knowledgeSummary = useMemo<ContextSummary>(() => {
    if (!ragEnabled || retrievalScope?.kind === "none" || !retrievalScope) {
      return {
        title: "未启用知识检索",
        detail: "任务将不引用课程或独立知识库",
      };
    }

    if (knowledgeState === "loading") {
      return { title: "正在读取知识范围", detail: "同步可检索资料状态" };
    }

    if (knowledgeState === "error") {
      return { title: "知识范围暂不可用", detail: "提交前可在任务定义区重试选择" };
    }

    if (retrievalScope.kind === "course") {
      const scopedCourse = courses.find(
        (item) =>
          item.id === retrievalScope.id ||
          item.knowledge_graph_id === retrievalScope.id,
      );
      return {
        title: scopedCourse?.name || "指定课程",
        detail: scopedCourse
          ? `${scopedCourse.ready_count}/${scopedCourse.document_count} 份课程资料可用`
          : "使用该课程关联的知识资料",
      };
    }

    if (retrievalScope.kind === "library") {
      const selectedLibrary = knowledgeBases.find(
        (item) => item.id === retrievalScope.id,
      );
      return {
        title: selectedLibrary?.name || "指定知识库",
        detail: selectedLibrary
          ? `${selectedLibrary.ready_count}/${selectedLibrary.document_count} 份资料可用`
          : "使用当前选定的独立知识库",
      };
    }

    const readyDocuments = knowledgeBases.reduce(
      (total, item) => total + item.ready_count,
      0,
    );
    const totalDocuments = knowledgeBases.reduce(
      (total, item) => total + item.document_count,
      0,
    );
    return {
      title: "全部可用知识库",
      detail: `${knowledgeBases.length} 个知识库 · ${readyDocuments}/${totalDocuments} 份资料可用`,
    };
  }, [courses, knowledgeBases, knowledgeState, ragEnabled, retrievalScope]);

  const courseDetail = (() => {
    if (course) {
      return `${course.ready_count}/${course.document_count} 份资料可用 · ${course.library_count} 个知识库`;
    }
    if (courseState === "loading") return "正在同步课程资料";
    if (courseState === "error") return "课程资料状态暂不可用";
    return "当前课程由知识图谱提供";
  })();

  const profileDetail = profile
    ? `${profile.knowledge_count} 个知识点 · 平均掌握 ${Math.round(
        profile.avg_mastery * 100,
      )}%`
    : "完成首个学习任务后开始记录";

  const pathDetail = plannedPath
    ? `${plannedPath.completed_count}/${plannedPath.nodes.length} 个节点已完成 · ${plannedPath.total_estimated_hours} 小时`
    : "尚未生成本课程的学习路径";

  const handleOpenTask = async (conversation: ConversationSummary) => {
    if (!userId || openingTaskId) return;
    setOpeningTaskId(conversation.session_id);
    setTaskError(null);
    try {
      if (conversation.session_id !== sessionId) {
        setSessionId(conversation.session_id);
      }
      if (conversation.message_count > 0) {
        await loadConversationAggregate(userId, conversation.session_id);
      }
      const hasContent = conversation.message_count > 0;
      onTaskSelected(hasContent);
      if (!hasContent) onCreateTask();
    } catch {
      setTaskError("任务打开失败，请稍后重试。");
    } finally {
      setOpeningTaskId(null);
    }
  };

  const visibleConversations = conversations.slice(0, 8);
  const readyCourseDocuments = course?.ready_count;

  return (
    <div className="min-h-full bg-bg-panel">
      <div className="mx-auto max-w-[1180px] px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
        <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_292px] xl:gap-0">
          <div className="min-w-0 xl:pr-8">
            <header className="border-b border-border pb-6">
              <div className="flex items-center gap-2 text-[11px] font-medium text-brand-600 dark:text-fg-muted">
                <BookOpen className="h-3.5 w-3.5" />
                课程任务工作台
              </div>
              <div className="mt-2 flex flex-col items-start justify-between gap-5 sm:flex-row sm:items-end">
                <div className="min-w-0">
                  <h1 className="text-[26px] font-semibold leading-tight tracking-[0.025em] text-fg sm:text-[30px]">
                    {courseName}
                  </h1>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-fg-muted text-pretty">
                    从课程目标建立学习任务，统一查看任务记录、知识范围与学习准备状态。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onCreateTask}
                  className="btn-primary h-9 shrink-0 px-3.5 text-sm"
                  aria-pressed={creating}
                >
                  <Plus className="h-4 w-4" />
                  {creating ? "正在定义任务" : "新建学习任务"}
                </button>
              </div>
            </header>

            <dl className="grid grid-cols-2 border-b border-border sm:grid-cols-3">
              <Metric
                label="任务记录"
                value={loading ? "—" : String(conversations.length)}
                detail="当前学习账户"
              />
              <Metric
                label="课程资料"
                value={readyCourseDocuments === undefined ? "—" : String(readyCourseDocuments)}
                detail={course ? `共 ${course.document_count} 份` : "等待课程数据"}
              />
              <Metric
                label="当前路径"
                value={plannedPath ? `${plannedPath.completed_count}/${plannedPath.nodes.length}` : "未规划"}
                detail={plannedPath ? plannedPath.name : "可建立路径任务"}
                className="col-span-2 border-t sm:col-span-1 sm:border-t-0"
              />
            </dl>

            <section className="pt-7" aria-labelledby="task-records-title">
              <div className="mb-3 flex items-end justify-between gap-4">
                <div>
                  <h2 id="task-records-title" className="text-sm font-semibold text-fg">
                    最近任务
                  </h2>
                  <p className="mt-1 text-xs text-fg-muted">
                    继续已有任务，或从当前课程建立新的工作项。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void loadWorkbench()}
                  disabled={loading}
                  className="inline-flex h-8 items-center gap-1.5 rounded px-2 text-xs text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg disabled:opacity-50"
                >
                  <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                  刷新
                </button>
              </div>

              <div className="border-t border-border">
                <div className="hidden grid-cols-[minmax(0,1fr)_92px_72px_118px_18px] gap-3 border-b border-border bg-bg-subtle px-3 py-2 text-[10px] font-medium text-fg-subtle sm:grid">
                  <span>任务</span>
                  <span>进展</span>
                  <span>记录</span>
                  <span>最近更新</span>
                  <span aria-hidden="true" />
                </div>

                {taskError && (
                  <div className="flex items-center justify-between gap-4 border-b border-border px-3 py-4 text-xs text-fg-muted">
                    <span>{taskError}</span>
                    <button
                      type="button"
                      onClick={() => void loadWorkbench()}
                      className="shrink-0 font-medium text-brand-600 hover:underline dark:text-fg"
                    >
                      重新加载
                    </button>
                  </div>
                )}

                {loading ? (
                  <div className="flex items-center gap-2 border-b border-border px-3 py-8 text-sm text-fg-muted">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    正在读取任务记录
                  </div>
                ) : taskError && conversations.length === 0 ? null : visibleConversations.length === 0 ? (
                  <div className="border-b border-border px-3 py-8">
                    <div className="text-sm font-medium text-fg">还没有学习任务</div>
                    <p className="mt-1 text-xs leading-5 text-fg-muted">
                      建立任务后，目标、执行记录和生成结果会归入同一工作项。
                    </p>
                    <button
                      type="button"
                      onClick={onCreateTask}
                      className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-brand-600 hover:underline dark:text-fg"
                    >
                      创建第一个任务
                      <ArrowRight className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ) : (
                  visibleConversations.map((conversation) => {
                    const isCurrent = conversation.session_id === sessionId;
                    const isOpening = openingTaskId === conversation.session_id;
                    const progress =
                      conversation.message_count === 0
                        ? "待定义"
                        : isCurrent
                          ? "当前任务"
                          : "可继续";
                    return (
                      <button
                        type="button"
                        key={conversation.session_id}
                        onClick={() => void handleOpenTask(conversation)}
                        disabled={openingTaskId !== null}
                        className={cn(
                          "group grid w-full grid-cols-[minmax(0,1fr)_18px] items-center gap-3 border-b border-border px-3 py-3.5 text-left transition-colors sm:grid-cols-[minmax(0,1fr)_92px_72px_118px_18px]",
                          isCurrent ? "bg-brand-50 dark:bg-bg-card" : "hover:bg-bg-subtle",
                        )}
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium text-fg">
                            {conversation.title || "未命名学习任务"}
                          </span>
                          <span className="mt-1 block truncate text-[11px] text-fg-muted">
                            {conversation.last_message_preview || "尚未填写任务目标"}
                          </span>
                          <span className="mt-2 flex items-center gap-3 text-[10px] text-fg-subtle sm:hidden">
                            <span>{progress}</span>
                            <span>{conversation.message_count} 条记录</span>
                            <span>{formatUpdatedAt(conversation.updated_at)}</span>
                          </span>
                        </span>
                        <span className="hidden items-center gap-2 text-xs text-fg-muted sm:flex">
                          <span
                            className={cn(
                              "h-1.5 w-1.5 rounded-full",
                              isCurrent ? "bg-brand-500 dark:bg-fg" : "bg-fg-subtle",
                            )}
                          />
                          {isOpening ? "打开中" : progress}
                        </span>
                        <span className="hidden text-xs tabular-nums text-fg-muted sm:block">
                          {conversation.message_count}
                        </span>
                        <span className="hidden text-xs text-fg-muted sm:block">
                          {formatUpdatedAt(conversation.updated_at)}
                        </span>
                        {isOpening ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-fg-subtle" />
                        ) : (
                          <ArrowRight className="h-3.5 w-3.5 text-fg-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-fg" />
                        )}
                      </button>
                    );
                  })
                )}
              </div>

              {conversations.length > visibleConversations.length && (
                <p className="mt-3 text-[11px] text-fg-subtle">
                  其余 {conversations.length - visibleConversations.length} 项可在左侧“最近任务”中打开。
                </p>
              )}
            </section>
          </div>

          <aside className="xl:border-l xl:border-border xl:pl-8" aria-label="任务上下文">
            <div className="border-b border-border pb-3">
              <h2 className="text-sm font-semibold text-fg">任务上下文</h2>
              <p className="mt-1 text-xs leading-5 text-fg-muted">
                提交任务时使用的课程与学习状态。
              </p>
            </div>
            <ContextRow
              icon={BookOpen}
              label="当前课程"
              title={courseName}
              detail={courseDetail}
            />
            <ContextRow
              icon={Database}
              label="知识范围"
              title={knowledgeSummary.title}
              detail={knowledgeSummary.detail}
            />
            <ContextRow
              icon={UserRound}
              label="学习状态"
              title={profile ? "状态已更新" : "等待首次记录"}
              detail={profileDetail}
            />
            <ContextRow
              icon={Route}
              label="学习路径"
              title={plannedPath?.name || "路径待规划"}
              detail={pathDetail}
            />
          </aside>
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  detail,
  className,
}: {
  label: string;
  value: string;
  detail: string;
  className?: string;
}) {
  return (
    <div className={cn("py-4 pr-4 sm:border-r sm:border-border sm:px-4 sm:first:pl-0 sm:last:border-r-0", className)}>
      <dt className="text-[10px] font-medium text-fg-subtle">{label}</dt>
      <dd className="mt-1 text-lg font-semibold tabular-nums text-fg">{value}</dd>
      <dd className="mt-0.5 truncate text-[10px] text-fg-muted">{detail}</dd>
    </div>
  );
}

function ContextRow({
  icon: Icon,
  label,
  title,
  detail,
}: {
  icon: typeof BookOpen;
  label: string;
  title: string;
  detail: string;
}) {
  return (
    <div className="border-b border-border py-4">
      <div className="flex items-center gap-2 text-[10px] font-medium text-fg-subtle">
        <Icon className="h-3.5 w-3.5 text-brand-500 dark:text-fg-muted" />
        {label}
      </div>
      <div className="mt-2 text-sm font-medium text-fg">{title}</div>
      <p className="mt-1 text-[11px] leading-5 text-fg-muted text-pretty">{detail}</p>
    </div>
  );
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
