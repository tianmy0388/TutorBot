/**
 * useJobQueue — Phase 5.2 async job client.
 *
 * Replaces the synchronous start_turn flow. Jobs are fire-and-forget:
 *
 *   submitJob("系统学习 Transformer")
 *     → opens a short WS, sends submit_job, receives ack, closes WS
 *     → returns { job_id, capability } immediately
 *     → background task runs server-side
 *
 *   subscribeJob(job_id)
 *     → opens a new WS, sends subscribe_job
 *     → streams events until done/cancelled
 *     → events flow through dispatchStreamEvent so the same chat/result
 *       routing as the legacy start_turn flow
 *
 * Pair with the existing job history (REST listJobs) to render the
 * persistent JobTray UI.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelJob as apiCancelJob,
  deleteJob as apiDeleteJob,
  getJobDetail,
  getJobStats,
  listJobs,
} from "@/lib/api";
import { useTutorStore } from "@/lib/store";
import { dispatchStreamEvent } from "@/lib/event-handler";
import { getJobIdFromEvent } from "@/lib/job-reducer";
import type { JobStatsResponse, JobSummary, JobStatus, StreamEvent } from "@/lib/types";
import { resolveWebSocketUrl, WsClient, startJobMessage } from "@/lib/ws";

export interface SubmitJobResult {
  job_id: string;
  capability: string;
  status: JobStatus;
  created_at: string;
}

export interface SubmitJobContext {
  sessionId?: string;
  webSearchRequested?: boolean;
}

export interface UseJobQueueState {
  jobs: JobSummary[];
  total: number;
  loading: boolean;
  error: string | null;
  stats: JobStatsResponse | null;
  activeJobs: JobSummary[];
  refresh: () => Promise<void>;
  submit: (
    text: string,
    capability?: string,
    context?: SubmitJobContext,
  ) => Promise<SubmitJobResult | null>;
  subscribe: (
    jobId: string,
    capabilityHint?: string,
    context?: Pick<SubmitJobContext, "sessionId">,
  ) => void;
  cancel: (jobId: string) => Promise<boolean>;
  remove: (jobId: string) => Promise<boolean>;
}

export function useJobQueue(userId: string | null | undefined): UseJobQueueState {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<JobStatsResponse | null>(null);
  const [tick, setTick] = useState(0); // bump on optimistic updates to re-render
  const jobsRef = useRef<JobSummary[]>([]);

  // In-flight subscribers (so we can tear them down on unmount)
  const liveClients = useRef<Map<string, WsClient>>(new Map());

  const refresh = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setError(null);
    try {
      const [listResp, statsResp] = await Promise.all([
        listJobs(userId, { limit: 50 }),
        getJobStats(userId).catch(() => null),
      ]);
      jobsRef.current = listResp.items;
      setJobs(listResp.items);
      setTotal(listResp.total);
      for (const item of listResp.items) {
        useTutorStore.getState().rehydrateJobFromDetail(item);
      }
      if (statsResp) setStats(statsResp);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  const submit = useCallback(
    async (
      text: string,
      capability?: string,
      context?: SubmitJobContext,
    ): Promise<SubmitJobResult | null> => {
      if (!text.trim() || typeof window === "undefined") return null;

      // Freeze the turn context before the asynchronous WebSocket handshake.
      // Navigation may change the global store before onOpen fires, but the
      // submitted job must remain attached to the conversation it came from.
      const stateAtSubmit = useTutorStore.getState();
      const submittedSessionId =
        context?.sessionId ?? stateAtSubmit.sessionId ?? "";
      const submittedWebSearch =
        context?.webSearchRequested ?? Boolean(stateAtSubmit.webSearchEnabled);
      const submittedLanguage = stateAtSubmit.language || "zh";
      const submittedKnowledgeBaseId =
        stateAtSubmit.activeKnowledgeBaseId || "ai_introduction";
      const submittedRagEnabled = stateAtSubmit.ragEnabled !== false;
      const submittedRetrievalScope = stateAtSubmit.retrievalScope;

      return new Promise<SubmitJobResult | null>((resolve) => {
        const url = resolveWebSocketUrl();
        const client = new WsClient({
          url,
          onOpen: () => {
            // 2026-06-21 plan (D10): the new TutoringCapability /
            // RetrievalService pair reads ``retrieval_scope`` from the
            // job metadata. The shape is the canonical
            // ``{kind, id}`` from the spec — "course", "library",
            // or "all" — and we forbid an empty / undefined scope
            // silently defaulting to "all": if the user hasn't
            // picked a scope we send "none" so the backend raises
            // a structured ``INVALID_SCOPE`` error rather than
            // searching the entire corpus. The user-facing toggle
            // in the chat composer is the only place that should
            // resolve "none" → a real scope.
            const ragEnabled = submittedRagEnabled;
            const scopeObj = submittedRetrievalScope;
            let scopeWire: string;
            if (!ragEnabled) {
              scopeWire = "none";
            } else if (
              scopeObj?.kind === "course" ||
              scopeObj?.kind === "library"
            ) {
              scopeWire = `${scopeObj.kind}:${scopeObj.id}`;
            } else if (scopeObj?.kind === "all") {
              scopeWire = "all";
            } else if (scopeObj?.kind === "none") {
              scopeWire = "none";
            } else {
              scopeWire = "all";
            }
            // 2026-06-21 plan: the Job must carry the same session_id as
            // the active conversation so right-pane resources, jobs, and
            // messages are joined correctly when the user switches
            // history. Capture the value for this turn before the async
            // WebSocket handshake and surface it both in the
            // top-level ``session_id`` field (consumed by JobSubmit on
            // the backend) and in ``metadata.session_id`` for legacy
            // callers that read it from the envelope.
            client.send(
              startJobMessage({
                message: text,
                userId: userId || "anonymous",
                capability: capability || undefined,
                sessionId: submittedSessionId,
                language: submittedLanguage,
                metadata: {
                  knowledge_base_id: submittedKnowledgeBaseId,
                  plan_id: "",
                  session_id: submittedSessionId,
                  retrieval_scope: scopeWire,
                  rag_enabled: ragEnabled,
                  web_search_requested: submittedWebSearch,
                },
              }),
            );
          },
          onEvent: (ev: any) => {
            if (ev.type === "job_submitted") {
              const result: SubmitJobResult = {
                job_id: ev.job_id,
                capability: ev.capability,
                status: ev.status,
                created_at: ev.created_at,
              };
              // Insert the job into the per-job reducer state so the
              // chat can immediately show a pending card and any
              // streamed events know where to land.
              if (useTutorStore.getState().sessionId === submittedSessionId) {
                useTutorStore.getState().applyReducerEvent({
                  type: "submit",
                  job_id: result.job_id,
                  capability: result.capability,
                  message_preview:
                    text.length > 60 ? text.slice(0, 60) + "…" : text,
                });
              }
              // Optimistic insert into local list (so the JobTray shows
              // the new pending job immediately, before refresh() runs).
              setJobs((prev) => {
                if (prev.some((p) => p.job_id === result.job_id)) return prev;
                const optimistic: JobSummary = {
                  job_id: result.job_id,
                  user_id: userId || "anonymous",
                  session_id: ev.session_id || submittedSessionId,
                  capability: result.capability,
                  status: "pending",
                  message_preview:
                    text.length > 60 ? text.slice(0, 60) + "…" : text,
                  language: submittedLanguage,
                  web_search_enabled: submittedWebSearch,
                  web_search_requested: submittedWebSearch,
                  event_count: 0,
                  created_at: result.created_at,
                  started_at: null,
                  finished_at: null,
                  duration_seconds: null,
                  has_result: false,
                  error: null,
                };
                const next = [optimistic, ...prev];
                jobsRef.current = next;
                return next;
              });
              setTotal((t) => t + 1);
              setTick((x) => x + 1);
              resolve(result);
              // Close the submit-leg WS shortly after ack
              setTimeout(() => client.close(), 200);
            } else if (ev.type === "error") {
              setError(ev.content || "submit failed");
              resolve(null);
              client.close();
            }
          },
          onClose: () => {},
          onError: () => {
            setError("WebSocket connection failed");
            resolve(null);
          },
        });
        client.connect();
      });
    },
    [userId],
  );

  const subscribe = useCallback(
    (
      jobId: string,
      _capabilityHint?: string,
      context?: Pick<SubmitJobContext, "sessionId">,
    ) => {
      if (!jobId) return;
      if (liveClients.current.has(jobId)) return; // already subscribed
      if (typeof window === "undefined") return;

      // Re-hydrate the per-job state from the REST snapshot first so
      // late subscribers see the same terminal assistant message that
      // a previous tab already produced. This is replay-safe: the
      // reducer skips an assistant message it has already inserted.
      (async () => {
        try {
          const detail = await getJobDetail(userId || "anonymous", jobId);
          if (detail) {
            useTutorStore.getState().rehydrateJobFromDetail(detail);
          }
        } catch (e) {
          // snapshot fetch is best-effort; the WS replay will still
          // bring the events.
          console.warn(`[useJobQueue] snapshot fetch failed for ${jobId}`, e);
        }
      })();

      const url = resolveWebSocketUrl();
      const client = new WsClient({
        url,
        onOpen: () => {
          client.send({ type: "subscribe_job", job_id: jobId });
        },
        onEvent: (ev: any) => {
          // Reuse the same dispatch pipeline so chat messages + result
          // routing work without duplication.
          const streamEv = ev as StreamEvent;
          const derivedJobId = getJobIdFromEvent(streamEv);
          // If the event is missing job_id but we know the WS context
          // (we subscribed to this job), attach it so the reducer is
          // not put in a protocol-error state.
          if (!derivedJobId && streamEv.metadata) {
            streamEv.metadata = { ...streamEv.metadata, job_id: jobId };
          }
          dispatchStreamEvent(streamEv, {
            sessionId: context?.sessionId,
            userId: userId || "anonymous",
          });
          if (streamEv.type === "stage_start") {
            setJobs((prev) => {
              const next: JobSummary[] = prev.map((j) =>
                j.job_id === jobId && j.status === "pending"
                  ? {
                      ...j,
                      status: "running",
                      started_at: new Date().toISOString(),
                    }
                  : j,
              );
              jobsRef.current = next;
              return next;
            });
          } else if (
            streamEv.type === "job_terminal" ||
            streamEv.type === "done" ||
            streamEv.type === "cancelled"
          ) {
            setTimeout(() => refresh(), 100);
            liveClients.current.delete(jobId);
            client.close();
          }
        },
        onClose: () => {
          liveClients.current.delete(jobId);
        },
        onError: () => {
          liveClients.current.delete(jobId);
        },
      });
      liveClients.current.set(jobId, client);
      client.connect();
    },
    [userId, refresh],
  );

  const cancel = useCallback(
    async (jobId: string): Promise<boolean> => {
      if (!userId) return false;
      try {
        await apiCancelJob(userId, jobId);
        const live = liveClients.current.get(jobId);
        if (live) {
          live.close();
          liveClients.current.delete(jobId);
        }
        await refresh();
        return true;
      } catch (e) {
        console.warn(`[useJobQueue] cancel(${jobId}) failed`, e);
        return false;
      }
    },
    [userId, refresh],
  );

  const remove = useCallback(
    async (jobId: string): Promise<boolean> => {
      if (!userId) return false;
      const removeFromQueue = () => {
        const existed = jobsRef.current.some((job) => job.job_id === jobId);
        const next = jobsRef.current.filter((job) => job.job_id !== jobId);
        jobsRef.current = next;
        setJobs(next);
        if (existed) setTotal((total) => Math.max(0, total - 1));
      };
      try {
        await apiDeleteJob(userId, jobId);
        removeFromQueue();
        useTutorStore.getState().removeJob(jobId);
        return true;
      } catch (e) {
        if ((e as { status?: number }).status === 404) {
          removeFromQueue();
          useTutorStore.getState().removeJob(jobId);
          return true;
        }
        console.warn(`[useJobQueue] remove(${jobId}) failed`, e);
        return false;
      }
    },
    [userId],
  );

  // Initial fetch + periodic refresh while there are active jobs.
  useEffect(() => {
    refresh();
    const t = setInterval(() => {
      refresh();
    }, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  // Compute the active list on each render (cheap O(n) over ≤50 rows)
  const activeJobs = jobs.filter(
    (j) => j.status === "pending" || j.status === "running",
  );

  // touch tick so optimistic state changes trigger a re-render
  void tick;

  return {
    jobs,
    total,
    loading,
    error,
    stats,
    activeJobs,
    refresh,
    submit,
    subscribe,
    cancel,
    remove,
  };
}
