/**
 * useKG — load course graph and plan a learner's path.
 */

"use client";

import { useEffect, useState } from "react";
import {
  getCourseGraph,
  listCourses,
  planPath,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import type { CourseGraph, LearnerProfileDetail, PlannedPath } from "@/lib/types";

export function useKG(): {
  courses: string[];
  currentCourse: string;
  graph: CourseGraph | null;
  plannedPath: PlannedPath | null;
  loading: boolean;
  error: string | null;
  refreshCourses: () => Promise<void>;
  loadGraph: (course?: string) => Promise<void>;
  plan: (profile: LearnerProfileDetail, course?: string, pathId?: string) => Promise<void>;
} {
  const currentCourse = useTutorStore((s) => s.currentCourse);
  const plannedPath = useTutorStore((s) => s.plannedPath);
  const setPlannedPath = useTutorStore((s) => s.setPlannedPath);
  const setCurrentCourse = (course: string) =>
    useTutorStore.setState({ currentCourse: course });

  const [courses, setCourses] = useState<string[]>([]);
  const [graph, setGraph] = useState<CourseGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshCourses = async () => {
    try {
      const res = await listCourses();
      setCourses(res.courses);
      if (res.courses.length > 0 && !res.courses.includes(currentCourse)) {
        setCurrentCourse(res.courses[0]);
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  const loadGraph = async (course?: string) => {
    const c = course || currentCourse;
    if (!c) return;
    setLoading(true);
    setError(null);
    try {
      const g = await getCourseGraph(c);
      setGraph(g);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const plan = async (
    profile: LearnerProfileDetail,
    course?: string,
    pathId?: string,
  ) => {
    const c = course || currentCourse;
    if (!c) return;
    try {
      const p = await planPath(c, profile, pathId || "");
      setPlannedPath(p);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  useEffect(() => {
    void refreshCourses();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (currentCourse) void loadGraph(currentCourse);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentCourse]);

  return {
    courses,
    currentCourse,
    graph,
    plannedPath,
    loading,
    error,
    refreshCourses,
    loadGraph,
    plan,
  };
}
