"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getLearningPath } from "@/lib/api";
import { useTutorStore } from "@/lib/store";

export function useLearningPath(userId?: string) {
  const fallbackUserId = useTutorStore((state) => state.userId);
  const path = useTutorStore((state) => state.plannedPath);
  const setPath = useTutorStore((state) => state.setPlannedPath);
  const target = userId ?? fallbackUserId;
  const generation = useRef(0);
  const [loaded, setLoaded] = useState(path !== null);
  const [loading, setLoading] = useState(path === null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!target) return;
    const request = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const next = await getLearningPath(target);
      if (request !== generation.current) return;
      setPath(next);
      setLoaded(true);
    } catch (reason) {
      if (request !== generation.current) return;
      setError(reason instanceof Error ? reason.message : String(reason));
      setLoaded(true);
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, [setPath, target]);

  useEffect(() => {
    return () => {
      generation.current += 1;
    };
  }, [target]);

  useEffect(() => {
    if (!path) void refresh();
  }, [path, refresh]);

  const status = error
    ? "failed"
    : path
      ? "success"
      : loaded
        ? "empty"
        : "loading";
  return { path, status, loading, error, refresh } as const;
}
