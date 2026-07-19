"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, BookOpen, Check, Clock3, FileText, Library, Loader2 } from "lucide-react";
import {
  getProfile,
  listAppCourses,
  listConversations,
  listResourcePackages,
  type ConversationSummary,
  type CourseResponse,
} from "@/lib/api";
import type { ResourcePackageSummary } from "@/lib/types";
import { useTutorStore } from "@/lib/store";

export default function LearningHomePage() {
  const userId = useTutorStore((state) => state.userId);
  const currentCourse = useTutorStore((state) => state.currentCourse);
  const profile = useTutorStore((state) => state.profile);
  const plannedPath = useTutorStore((state) => state.plannedPath);
  const setProfile = useTutorStore((state) => state.setProfile);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [packages, setPackages] = useState<ResourcePackageSummary[]>([]);
  const [courses, setCourses] = useState<CourseResponse[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    void Promise.allSettled([
      listConversations(userId, { limit: 3 }),
      listResourcePackages(userId, { limit: 3 }),
      listAppCourses(),
      profile ? Promise.resolve(profile) : getProfile(userId),
    ]).then(([conversationResult, packageResult, courseResult, profileResult]) => {
      if (cancelled) return;
      if (conversationResult.status === "fulfilled") setConversations(conversationResult.value.items);
      if (packageResult.status === "fulfilled") setPackages(packageResult.value.items);
      if (courseResult.status === "fulfilled") setCourses(courseResult.value.items);
      if (profileResult.status === "fulfilled" && profileResult.value) setProfile(profileResult.value);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [profile, setProfile, userId]);

  const course = useMemo(
    () => courses.find((item) => item.knowledge_graph_id === currentCourse || item.id === currentCourse),
    [courses, currentCourse],
  );
  const recentConversation = conversations[0];
  const recentPackage = packages[0];
  const weakConcept = profile?.weak_concepts[0];

  return (
    <div className="h-full overflow-y-auto bg-bg">
      <div className="relative isolate mx-auto min-h-full max-w-[1440px] overflow-hidden px-5 py-8 sm:px-8 sm:py-10 lg:px-12 lg:py-14">
        <div className="breathing-orb breathing-orb-one" aria-hidden="true" />
        <div className="breathing-orb breathing-orb-two" aria-hidden="true" />

        <header className="relative z-10 flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-semibold text-fg-muted">欢迎回来</p>
            <h1 className="mt-2 max-w-3xl text-[38px] font-bold leading-[1.08] tracking-[0.035em] text-fg sm:text-[52px] lg:text-[64px]">
              今天，想从哪里继续？
            </h1>
          </div>
          {loading && <Loader2 className="h-5 w-5 animate-spin text-fg-muted" aria-label="正在同步学习状态" />}
        </header>

        <main className="relative z-10 mt-10 grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(340px,0.85fr)] xl:gap-8">
          <section className="overflow-hidden rounded-[32px] bg-[rgb(var(--color-brand-500))] p-6 text-[rgb(var(--color-hero-fg))] shadow-[var(--shadow-soft)] sm:p-8 lg:p-10">
            <div className="flex items-start justify-between gap-6">
              <div>
                <p className="text-sm font-semibold opacity-75">继续学习</p>
                <h2 className="mt-4 max-w-2xl text-3xl font-bold leading-tight tracking-[0.03em] sm:text-4xl">
                  {recentConversation?.title || course?.name || "从一门课程开始"}
                </h2>
                <p className="mt-4 max-w-xl text-sm leading-7 opacity-80 sm:text-base">
                  {recentConversation?.last_message_preview || course?.description || "选择课程资料，告诉 TutorBot 你现在想弄懂的问题。"}
                </p>
              </div>
              <div className="hidden h-24 w-24 shrink-0 items-center justify-center rounded-full bg-white/20 sm:flex">
                <BookOpen className="h-10 w-10" />
              </div>
            </div>

            {plannedPath && (
              <div className="mt-8 border-t border-current/20 pt-5 text-sm">
                <div className="flex items-center justify-between gap-4">
                  <span>{plannedPath.name}</span>
                  <span className="font-semibold">{plannedPath.completed_count}/{plannedPath.nodes.length} 已完成</span>
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-current/15">
                  <div className="h-full rounded-full bg-current" style={{ width: `${plannedPath.nodes.length ? (plannedPath.completed_count / plannedPath.nodes.length) * 100 : 0}%` }} />
                </div>
              </div>
            )}

            <Link href="/workspace" className="mt-8 inline-flex min-h-12 items-center gap-2 rounded-full bg-[rgb(var(--color-fg))] px-6 text-sm font-bold text-[rgb(var(--color-bg-panel))] transition-transform duration-300 hover:-translate-y-0.5">
              {recentConversation ? "回到上次学习" : "开始学习"}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </section>

          <section className="rounded-[32px] bg-bg-panel p-6 sm:p-8">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-fg-muted">今日安排</p>
                <h2 className="mt-2 text-2xl font-bold tracking-[0.025em]">把注意力放在下一步</h2>
              </div>
              <Clock3 className="h-6 w-6 text-fg-muted" />
            </div>
            <ol className="mt-6 space-y-1">
              <TodayItem checked={false} title={recentConversation ? "继续上次的学习任务" : "建立第一个学习任务"} detail={recentConversation?.title || course?.name || "选择一门课程"} />
              <TodayItem checked={false} title={weakConcept ? `复习：${weakConcept}` : "确认一个还没掌握的问题"} detail={profile ? "来自你的学习记录" : "完成练习后会在这里整理"} />
              <TodayItem checked={false} title={recentPackage ? "打开最近整理的资料" : "把课程资料放进资料库"} detail={recentPackage?.topic || "PDF、PPT、Markdown 或文本"} />
            </ol>
          </section>

          <section className="rounded-[32px] bg-bg-panel p-6 sm:p-8 xl:col-span-2">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-fg-muted">最近资料</p>
                <h2 className="mt-2 text-2xl font-bold tracking-[0.025em]">需要时，再把它们拿起来</h2>
              </div>
              <Link href="/knowledge-bases" className="inline-flex min-h-11 items-center gap-2 rounded-full px-4 text-sm font-semibold text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg">
                查看资料库 <ArrowRight className="h-4 w-4" />
              </Link>
            </div>

            <div className="mt-6 grid gap-3 md:grid-cols-3">
              {packages.length > 0 ? packages.slice(0, 3).map((item) => (
                <Link key={item.package_id} href="/resources" className="group rounded-3xl bg-bg-subtle p-5 transition-transform duration-300 hover:-translate-y-1">
                  <FileText className="h-5 w-5 text-fg-muted" />
                  <h3 className="mt-5 line-clamp-2 font-bold">{item.topic}</h3>
                  <p className="mt-2 text-xs text-fg-muted">{item.resource_count} 份资料 · {item.total_minutes} 分钟</p>
                </Link>
              )) : (
                <div className="rounded-3xl bg-bg-subtle p-5 md:col-span-3">
                  <Library className="h-5 w-5 text-fg-muted" />
                  <h3 className="mt-5 font-bold">这里还很安静</h3>
                  <p className="mt-2 text-sm leading-6 text-fg-muted">开始一次学习任务，或先上传课程资料。最近使用的内容会留在这里。</p>
                </div>
              )}
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

function TodayItem({ checked, title, detail }: { checked: boolean; title: string; detail: string }) {
  return (
    <li className="flex gap-3 border-b border-border py-4 last:border-0">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border bg-bg-subtle">
        {checked && <Check className="h-3.5 w-3.5" />}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-bold">{title}</p>
        <p className="mt-1 truncate text-xs text-fg-muted">{detail}</p>
      </div>
    </li>
  );
}
