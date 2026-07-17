/**
 * useProfile — load and watch a learner's profile.
 *
 * MVP: profile is not exposed via REST (we focused on the KG plan endpoint
 * for Phase 4). This hook exposes a refresh helper that consumers can
 * trigger after a turn completes; once the backend exposes
 * `GET /api/v1/profile/{user_id}` we'll wire it here.
 *
 * A 404 from the backend means the user has no profile yet — treat
 * that as "loaded with no data" instead of a loud error. The previous
 * version used the literal string "anonymous" as the default userId,
 * which never matched a real profile and produced 404s in the server
 * log on every page load.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTutorStore } from "@/lib/store";
import { ApiError, getProfile } from "@/lib/api";
import type { LearnerProfileDetail } from "@/lib/types";

export function useProfile(userId?: string): {
  profile: LearnerProfileDetail | null;
  loaded: boolean;
  loading: boolean;
  error: string | null;
  status: "loading" | "empty" | "success" | "failed";
  refresh: () => Promise<void>;
} {
  const cachedProfile = useTutorStore((s) => s.profile);
  const cacheOwner = useTutorStore((s) => s.profileOwnerId);
  const cacheLoaded = useTutorStore((s) => s.profileLoaded);
  const setProfile = useTutorStore((s) => s.setProfile);
  const fallbackUserId = useTutorStore((s) => s.userId);
  const target = userId ?? fallbackUserId;

  const [requestState, setRequestState] = useState<{
    target: string | null;
    loading: boolean;
    error: string | null;
  }>({ target: null, loading: false, error: null });
  const requestGeneration = useRef(0);

  const ownsCache = Boolean(target) && cacheOwner === target;
  const profile = ownsCache ? cachedProfile : null;
  const loaded = ownsCache && cacheLoaded;
  const requestIsCurrent = requestState.target === target;
  const error = requestIsCurrent ? requestState.error : null;
  const loading = Boolean(target) && (
    (requestIsCurrent && requestState.loading) || (!loaded && !requestIsCurrent)
  );

  const refresh = useCallback(async () => {
    if (!target) return;
    const generation = ++requestGeneration.current;
    setRequestState({ target, loading: true, error: null });
    try {
      const p = await getProfile(target);
      if (generation !== requestGeneration.current) return;
      setProfile(p, target);
    } catch (e: any) {
      if (generation !== requestGeneration.current) return;
      // 404 = the user simply has no profile yet. That's a normal
      // first-load state, not a failure; surface it as no profile
      // instead of an error.
      if (e instanceof ApiError && e.status === 404) {
        setProfile(null, target);
        return;
      }
      setRequestState({
        target,
        loading: true,
        error: e?.message || String(e),
      });
    } finally {
      if (generation === requestGeneration.current) {
        setRequestState((state) =>
          state.target === target ? { ...state, loading: false } : state,
        );
      }
    }
  }, [setProfile, target]);

  useEffect(() => {
    return () => {
      requestGeneration.current += 1;
    };
  }, [target]);

  useEffect(() => {
    if (!loaded && target) {
      void refresh();
    }
  }, [loaded, refresh, target]);

  const status = loading
    ? "loading"
    : error
      ? "failed"
      : profile
        ? "success"
        : loaded
          ? "empty"
          : "loading";
  return { profile, loaded, loading, error, status, refresh };
}
