/**
 * useResourceHistory — fetch a user's generated resource packages
 * (Phase 5 persistence layer).
 *
 * - On mount, lazy-loads the user's package summaries
 * - `refresh()` re-fetches (call after a new generation finishes)
 * - `loadDetail(packageId)` lazily loads full payload (header + all resources)
 *   and caches it in-memory
 * - `stats()` exposes aggregate stats (package count, total minutes, type mix)
 *
 * Pair with `setLatestPackage` in the store when a new package arrives via
 * the WebSocket, then call `refresh()` to update the list.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteResourcePackage,
  getResourcePackageDetail,
  getResourcePackageStats,
  listResourcePackages,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import type {
  PackageStatsResponse,
  ResourcePackage,
  ResourcePackageSummary,
} from "@/lib/types";

export interface UseResourceHistoryState {
  /** Lightweight summaries (newest first). */
  packages: ResourcePackageSummary[];
  total: number;
  loading: boolean;
  error: string | null;
  /** Aggregate stats for the user. */
  stats: PackageStatsResponse | null;
  /** In-memory cache: packageId → full ResourcePackage. */
  detailCache: Map<string, ResourcePackage>;
  /** Force re-fetch from the backend. */
  refresh: () => Promise<void>;
  /** Load (and cache) the full payload for one package. */
  loadDetail: (packageId: string) => Promise<ResourcePackage | null>;
  /** Remove a package from server + local cache. */
  remove: (packageId: string) => Promise<boolean>;
}

export function useResourceHistory(
  userId: string | null | undefined,
): UseResourceHistoryState {
  const [packages, setPackages] = useState<ResourcePackageSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<PackageStatsResponse | null>(null);
  const detailCache = useRef<Map<string, ResourcePackage>>(new Map());

  const setLatestPackage = useTutorStore((s) => s.setLatestPackage);

  const refresh = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setError(null);
    try {
      const [listResp, statsResp] = await Promise.all([
        listResourcePackages(userId, { limit: 50 }),
        getResourcePackageStats(userId).catch(() => null),
      ]);
      setPackages(listResp.items);
      setTotal(listResp.total);
      if (statsResp) setStats(statsResp);
      // Invalidate cached details that are no longer in the list (e.g. deleted)
      const ids = new Set(listResp.items.map((p) => p.package_id));
      for (const k of detailCache.current.keys()) {
        if (!ids.has(k)) detailCache.current.delete(k);
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  const loadDetail = useCallback(
    async (packageId: string): Promise<ResourcePackage | null> => {
      if (!userId) return null;
      const cached = detailCache.current.get(packageId);
      if (cached) return cached;
      try {
        const full = await getResourcePackageDetail(userId, packageId);
        detailCache.current.set(packageId, full);
        return full;
      } catch (e) {
        console.warn(`[useResourceHistory] loadDetail(${packageId}) failed`, e);
        return null;
      }
    },
    [userId],
  );

  const remove = useCallback(
    async (packageId: string): Promise<boolean> => {
      if (!userId) return false;
      try {
        const resp = await deleteResourcePackage(userId, packageId);
        detailCache.current.delete(packageId);
        setPackages((prev) => prev.filter((p) => p.package_id !== packageId));
        setTotal((prev) => Math.max(0, prev - 1));
        // If the deleted package is the latest in the store, clear it
        const cur = useTutorStore.getState().latestPackage;
        if (cur && cur.package_id === packageId) {
          setLatestPackage(null);
        }
        return resp.deleted;
      } catch (e) {
        console.warn(`[useResourceHistory] remove(${packageId}) failed`, e);
        return false;
      }
    },
    [userId, setLatestPackage],
  );

  // Auto-load on mount and whenever userId changes
  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    packages,
    total,
    loading,
    error,
    stats,
    detailCache: detailCache.current,
    refresh,
    loadDetail,
    remove,
  };
}