/**
 * useProfile — load and watch a learner's profile.
 *
 * MVP: profile is not exposed via REST (we focused on the KG plan endpoint
 * for Phase 4). This hook exposes a refresh helper that consumers can
 * trigger after a turn completes; once the backend exposes
 * `GET /api/v1/profile/{user_id}` we'll wire it here.
 */

"use client";

import { useEffect, useState } from "react";
import { useTutorStore } from "@/lib/store";
import { getProfile } from "@/lib/api";
import type { LearnerProfileDetail } from "@/lib/types";

export function useProfile(userId?: string): {
  profile: LearnerProfileDetail | null;
  loaded: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
} {
  const profile = useTutorStore((s) => s.profile);
  const loaded = useTutorStore((s) => s.profileLoaded);
  const setProfile = useTutorStore((s) => s.setProfile);
  const fallbackUserId = useTutorStore((s) => s.userId);
  const target = userId ?? fallbackUserId;

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    if (!target) return;
    setLoading(true);
    setError(null);
    try {
      const p = await getProfile(target);
      setProfile(p);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!loaded && target) {
      void refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  return { profile, loaded, loading, error, refresh };
}
