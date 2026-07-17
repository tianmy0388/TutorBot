"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getLearningPath } from "@/lib/api";
import { useTutorStore } from "@/lib/store";

export function useLearningPath(userId?: string) {
  const fallbackUserId = useTutorStore((state) => state.userId);
  const cachedPath = useTutorStore((state) => state.plannedPath);
  const cacheOwner = useTutorStore((state) => state.plannedPathOwnerId);
  const cacheLoaded = useTutorStore((state) => state.plannedPathLoaded);
  const profile = useTutorStore((state) => state.profile);
  const profileOwner = useTutorStore((state) => state.profileOwnerId);
  const setPath = useTutorStore((state) => state.setPlannedPath);
  const target = userId ?? fallbackUserId;
  const generation = useRef(0);
  const [requestState, setRequestState] = useState<{
    target: string | null;
    loading: boolean;
    error: string | null;
  }>({ target: null, loading: false, error: null });

  const ownsCache = Boolean(target) && cacheOwner === target;
  const path = ownsCache ? cachedPath : null;
  const loaded = ownsCache && cacheLoaded;
  const requestIsCurrent = requestState.target === target;
  const error = requestIsCurrent ? requestState.error : null;
  const loading = Boolean(target) && (
    (requestIsCurrent && requestState.loading) || (!loaded && !requestIsCurrent)
  );
  const stale = Boolean(
    path &&
    profile &&
    profileOwner === target &&
    typeof path.profile_version === "number" &&
    path.profile_version < profile.version,
  );

  const refresh = useCallback(async () => {
    if (!target) return;
    const request = ++generation.current;
    setRequestState({ target, loading: true, error: null });
    try {
      const next = await getLearningPath(target);
      if (request !== generation.current) return;
      setPath(next, target);
    } catch (reason) {
      if (request !== generation.current) return;
      setRequestState({
        target,
        loading: true,
        error: reason instanceof Error ? reason.message : String(reason),
      });
    } finally {
      if (request === generation.current) {
        setRequestState((state) =>
          state.target === target ? { ...state, loading: false } : state,
        );
      }
    }
  }, [setPath, target]);

  useEffect(() => {
    return () => {
      generation.current += 1;
    };
  }, [target]);

  useEffect(() => {
    if (!loaded && target) void refresh();
  }, [loaded, refresh, target]);

  const status = loading
    ? "loading"
    : error
      ? "failed"
      : stale
        ? "stale"
        : path
          ? "success"
          : loaded
            ? "empty"
            : "loading";
  return { path, status, stale, loading, error, refresh } as const;
}
