/**
 * Zustand store — single source of truth for UI state.
 *
 * Slices:
 *  - session:     active session id, current turn events, latest result
 *  - profile:     last-loaded learner profile (lazy)
 *  - resources:   most-recent ResourcePackage + per-resource selection
 *  - kg:          current course + latest PlannedPath
 *  - assessment:  most-recent AssessmentReport + StrategyDecision
 *  - ui:          trace panel visibility, current capability, etc.
 */

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type {
  AssessmentReport,
  ChatMessage,
  LearnerProfileDetail,
  MessageRole,
  PlannedPath,
  QuestionUnderstanding,
  ResourcePackage,
  StreamEvent,
  StrategyDecision,
  TutoringAnswer,
  EnrichmentSuggestion,
} from "./types";
import { createJobState, reduceJobEvent, type JobsState } from "./job-reducer";

// Re-export so existing consumers can keep importing ChatMessage from store.
export type { ChatMessage, MessageRole };

// ---------------------------------------------------------------------------
// Active turn
// ---------------------------------------------------------------------------

export type TurnPhase =
  | "idle"
  | "connecting"
  | "streaming"
  | "success"
  | "error";

export type Theme = "dark" | "light";

export interface ActiveTurn {
  turn_id: string;
  phase: TurnPhase;
  started_at: number;
  events: StreamEvent[];
  text_buffer: string;
  thinking_buffer: string;
  result: Record<string, unknown> | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Resource selection
// ---------------------------------------------------------------------------

export interface ResourceSelection {
  packageId: string | null;
  selectedResourceId: string | null;
}

// ---------------------------------------------------------------------------
// Incoming stream event (loose shape — both StreamEvent and WSServerMessage)
// ---------------------------------------------------------------------------

/** Stream event with optional content/metadata (WSServerMessage-compatible). */
export type IncomingStreamEvent = StreamEvent | {
  type: import("./types").StreamEventType;
  source?: string;
  stage?: string;
  content?: string;
  metadata?: Record<string, unknown>;
  session_id?: string;
  turn_id?: string;
  seq?: number;
  timestamp?: number;
  event_id?: string;
};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export interface TutorState {
  // Session
  userId: string;
  sessionId: string;
  language: "zh" | "en";
  messages: ChatMessage[];
  activeTurn: ActiveTurn;

  // Jobs (Task 3: per-job state, replaces the single activeTurn model)
  jobsById: JobsState["jobsById"];
  jobOrder: JobsState["jobOrder"];

  // Profile
  profile: LearnerProfileDetail | null;
  profileLoaded: boolean;

  // Resources
  latestPackage: ResourcePackage | null;
  resourceSelection: ResourceSelection;

  // Knowledge graph
  currentCourse: string;
  plannedPath: PlannedPath | null;

  // Knowledge base (Task 9)
  activeKnowledgeBaseId: string;

  // 2026-06-21 plan (D10): explicit RAG scope.
  // ``retrievalScope`` is the user-picked scope; ``ragEnabled``
  // toggles the whole RAG path. The two together replace the
  // old ``activeKnowledgeBaseId`` field for vector retrieval
  // (which we keep as the legacy fallback for pre-fix
  // capabilities).
  ragEnabled: boolean;
  retrievalScope:
    | { kind: "all" }
    | { kind: "course"; id: string }
    | { kind: "library"; id: string }
    | { kind: "none" }
    | null;

  // Assessment
  latestAssessment: AssessmentReport | null;
  latestStrategy: StrategyDecision | null;

  // Tutor
  latestUnderstanding: QuestionUnderstanding | null;
  latestTutorAnswer: TutoringAnswer | null;
  latestEnrichments: EnrichmentSuggestion[];

  // UI
  tracePanelOpen: boolean;
  currentCapability: string | null;
  wsConnected: boolean;
  theme: Theme;
  settingsOpen: boolean;

  // ---- actions ----
  setUser: (userId: string) => void;
  setLanguage: (lang: "zh" | "en") => void;
  setCurrentCapability: (cap: string | null) => void;
  setTracePanelOpen: (open: boolean) => void;
  setWsConnected: (connected: boolean) => void;
  setTheme: (theme: Theme) => void;
  hydrateTheme: () => void;
  /** Generate a client-side session id post-hydration (SSR-safe). */
  hydrateSessionId: () => void;
  setSettingsOpen: (open: boolean) => void;
  setProfile: (p: LearnerProfileDetail | null) => void;
  setLatestPackage: (pkg: ResourcePackage | null) => void;
  selectResource: (resourceId: string | null) => void;
  setPlannedPath: (p: PlannedPath | null) => void;
  setActiveKnowledgeBaseId: (id: string) => void;
  // 2026-06-21 plan (D10): RAG scope setters.
  setRagEnabled: (enabled: boolean) => void;
  setRetrievalScope: (
    scope:
      | { kind: "all" }
      | { kind: "course"; id: string }
      | { kind: "library"; id: string }
      | { kind: "none" }
      | null,
  ) => void;
  setLatestAssessment: (a: AssessmentReport | null) => void;
  setLatestStrategy: (s: StrategyDecision | null) => void;
  setTutorResult: (
    understanding: QuestionUnderstanding | null,
    answer: TutoringAnswer | null,
    enrichments: EnrichmentSuggestion[],
  ) => void;

  // Chat actions
  addMessage: (msg: Omit<ChatMessage, "id" | "timestamp">) => void;
  startActiveTurn: (turnId: string, capability: string) => void;
  applyStreamEvent: (ev: IncomingStreamEvent) => void;
  completeActiveTurn: (result: Record<string, unknown> | null, error: string | null) => void;
  /** Internal: route a typed reducer event through the pure job reducer. */
  applyReducerEvent: (event: import("./job-reducer").ReducerEvent) => void;
  resetSession: () => void;

  // Conversation (2026-06-21 plan, stage 4)
  setSessionId: (sessionId: string) => void;
  loadConversationIntoStore: (userId: string, sessionId: string) => Promise<void>;
  /**
   * Atomic conversation-switch: replace messages + jobs + right-pane
   * state in a single store update so the UI does not flicker or
   * show another session's data. Background jobs running in other
   * sessions are preserved (the store's ``jobsById`` only contains
   * the jobs we observed via the live WS for the active session;
   * switching does not touch the running task — the next time the
   * user comes back to that session, the REST snapshot re-hydrates).
   */
  loadConversationAggregate: (userId: string, sessionId: string) => Promise<void>;
  /** Per-job helpers exposed so hooks (re)subscribing after a switch
   *  can re-hydrate from REST without duplicating reducer logic. */
  rehydrateJobFromDetail: (detail: {
    job_id: string;
    capability: string;
    status: import("./types").JobStatus;
    message_preview: string;
    created_at?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    events?: import("./types").StreamEvent[];
    event_count?: number;
    result?: unknown;
    error?: { code: string; message: string } | null;
  }) => void;
}

const newActiveTurn = (): ActiveTurn => ({
  turn_id: "",
  phase: "idle",
  started_at: 0,
  events: [],
  text_buffer: "",
  thinking_buffer: "",
  result: null,
  error: null,
});

let messageCounter = 0;
const nextMessageId = () =>
  `msg_${Date.now()}_${(messageCounter += 1).toString(36)}`;

export const useTutorStore = create<TutorState>()(
  subscribeWithSelector((set, get) => ({
    // --- state ---
    // userId is a stable per-browser id, persisted in localStorage so
    // refreshing the page keeps the same user. The previous default
    // was the literal string "anonymous" which never matched any real
    // user in the backend, so every page load issued a 404 against
    // /api/v1/profile/anonymous. Generate once on first read.
    userId: getOrCreateUserId(),
    // Start empty to avoid SSR hydration mismatch. The real UUID is
    // generated client-side only via ``hydrateSessionId`` (called from
    // page.tsx useEffect post-mount). The sidebar renders "connecting…"
    // until the id is assigned.
    sessionId: "",
    language: "zh",
    messages: [],
    activeTurn: newActiveTurn(),

    jobsById: {},
    jobOrder: [],

    profile: null,
    profileLoaded: false,

    latestPackage: null,
    resourceSelection: { packageId: null, selectedResourceId: null },

    currentCourse: "ai_introduction",
    plannedPath: null,
    activeKnowledgeBaseId: "ai_introduction",
    // 2026-06-21 plan (D10): default to RAG enabled, scope = all.
    // The chat composer renders a scope picker that overrides
    // these on a per-turn basis.
    ragEnabled: true,
    retrievalScope: { kind: "all" },

    latestAssessment: null,
    latestStrategy: null,

    latestUnderstanding: null,
    latestTutorAnswer: null,
    latestEnrichments: [],

    tracePanelOpen: true,
    currentCapability: null,
    wsConnected: false,
    theme: "dark",
    settingsOpen: false,

    // --- simple setters ---
    setUser: (userId) => set({ userId }),
    setLanguage: (language) => set({ language }),
    setCurrentCapability: (cap) => set({ currentCapability: cap }),
    setTracePanelOpen: (open) => set({ tracePanelOpen: open }),
    setWsConnected: (connected) => set({ wsConnected: connected }),
    setTheme: (theme) => {
      if (typeof document !== "undefined") {
        document.documentElement.dataset.theme = theme;
      }
      try {
        if (typeof window !== "undefined") {
          window.localStorage.setItem("tutor:theme", theme);
        }
      } catch {
        // localStorage may be blocked (private mode); silently ignore.
      }
      set({ theme });
    },
    hydrateTheme: () => {
      let theme: Theme = "dark";
      try {
        if (typeof window !== "undefined") {
          const stored = window.localStorage.getItem("tutor:theme");
          if (stored === "light" || stored === "dark") {
            theme = stored;
          }
        }
      } catch {
        // ignore
      }
      if (typeof document !== "undefined") {
        document.documentElement.dataset.theme = theme;
      }
      set({ theme });
    },
    setSettingsOpen: (open) => set({ settingsOpen: open }),
    setProfile: (p) => set({ profile: p, profileLoaded: p !== null }),
    setLatestPackage: (pkg) =>
      set((state) => ({
        latestPackage: pkg,
        resourceSelection:
          pkg && pkg.package_id !== state.resourceSelection.packageId
            ? {
                packageId: pkg.package_id,
                selectedResourceId: pkg.resources[0]?.resource_id || null,
              }
            : state.resourceSelection,
      })),
    selectResource: (resourceId) =>
      set((state) => ({
        resourceSelection: {
          ...state.resourceSelection,
          selectedResourceId: resourceId,
        },
      })),
    setPlannedPath: (p) => set({ plannedPath: p }),
    setActiveKnowledgeBaseId: (id) => set({ activeKnowledgeBaseId: id }),
    setRagEnabled: (enabled) => set({ ragEnabled: enabled }),
    setRetrievalScope: (scope) => set({ retrievalScope: scope }),
    setLatestAssessment: (a) => set({ latestAssessment: a }),
    setLatestStrategy: (s) => set({ latestStrategy: s }),
    setTutorResult: (understanding, answer, enrichments) =>
      set({
        latestUnderstanding: understanding,
        latestTutorAnswer: answer,
        latestEnrichments: enrichments,
      }),

    // --- chat actions ---
    addMessage: (msg) =>
      set((state) => ({
        messages: [
          ...state.messages,
          { ...msg, id: nextMessageId(), timestamp: Date.now() },
        ],
      })),

    startActiveTurn: (turn_id, capability) =>
      set({
        activeTurn: {
          ...newActiveTurn(),
          turn_id,
          phase: "streaming",
          started_at: Date.now(),
        },
        currentCapability: capability,
      }),

    applyStreamEvent: (ev) =>
      set((state) => {
        // Task 3: events are now owned by jobs (see job-reducer.ts). The
        // single-activeTurn heuristic was the root cause of the no-output
        // regression — we no longer drop events when no turn is "active".
        const md = (ev.metadata ?? {}) as Record<string, unknown>;
        const jobId = typeof md.job_id === "string" ? md.job_id : null;
        if (!jobId) {
          // Legacy / protocol error: surface it as a system message but
          // do NOT guess the owning job from currentCapability.
          return {
            messages: [
              ...state.messages,
              {
                id: nextMessageId(),
                role: "system",
                content: "协议错误：流事件缺少 job_id",
                timestamp: Date.now(),
                metadata: { protocol_error: true, event_type: ev.type },
              },
            ],
          };
        }
        // Normalise to a StreamEvent-shaped object for the reducer.
        const streamEv: StreamEvent = {
          type: ev.type,
          source: ev.source ?? "",
          stage: ev.stage ?? "",
          content: ev.content ?? "",
          metadata: ev.metadata ?? {},
          session_id: ev.session_id ?? "",
          turn_id: ev.turn_id ?? "",
          seq: ev.seq ?? 0,
          timestamp: ev.timestamp ?? Date.now() / 1000,
          event_id: ev.event_id ?? "",
        };
        const next = reduceJobEvent(
          { jobsById: state.jobsById, jobOrder: state.jobOrder, messages: state.messages },
          { type: "stream", event: streamEv, job_id: jobId },
        );
        return {
          jobsById: next.jobsById,
          jobOrder: next.jobOrder,
          messages: next.messages,
        };
      }),

    applyReducerEvent: (event) =>
      set((state) => {
        const next = reduceJobEvent(
          { jobsById: state.jobsById, jobOrder: state.jobOrder, messages: state.messages },
          event,
        );
        return {
          jobsById: next.jobsById,
          jobOrder: next.jobOrder,
          messages: next.messages,
        };
      }),

    completeActiveTurn: (result, error) =>
      set((state) => {
        const turn = state.activeTurn;
        const phase: TurnPhase = error ? "error" : "success";
        const assistantMsg: ChatMessage | null =
          turn.text_buffer || turn.thinking_buffer
            ? {
                id: nextMessageId(),
                role: "assistant",
                agent: state.currentCapability || undefined,
                content: turn.text_buffer || turn.thinking_buffer,
                timestamp: Date.now(),
              }
            : null;
        return {
          activeTurn: { ...turn, phase, result, error, events: turn.events },
          messages: assistantMsg
            ? [...state.messages, assistantMsg]
            : state.messages,
          currentCapability: null,
        };
      }),

    resetSession: () =>
      set({
        // 2026-06-21 plan: resetSession clears the visible UI state
        // (messages, jobs, right-pane panels) but DOES NOT touch
        // ``sessionId``. The caller is responsible for assigning a
        // new id explicitly (``setSessionId``) before invoking this
        // action. The id stays as-is (e.g. empty on first load,
        // or the server-assigned id from a new-conversation POST).
        messages: [],
        activeTurn: newActiveTurn(),
        jobsById: {},
        jobOrder: [],
        latestPackage: null,
        resourceSelection: { packageId: null, selectedResourceId: null },
        plannedPath: null,
        latestAssessment: null,
        latestStrategy: null,
        latestUnderstanding: null,
        latestTutorAnswer: null,
        latestEnrichments: [],
      }),
    /**
     * Hydrate the active session id on first mount. Order:
     *   1. If the in-memory ``state.sessionId`` is non-empty, keep it.
     *   2. Otherwise check ``localStorage.tutor:lastSessionId`` — this
     *      is what survives a browser refresh (Bug 4 fix, 2026-07-09).
     *   3. Otherwise mint a new UUID client-side.
     *
     * Must only run in the browser (post-hydration) to avoid the SSR
     * mismatch that the old ``cryptoRandom()`` initialiser produced
     * — the server rendered ``s_o0vtoy…`` (Math.random) and the
     * client showed ``020d682f…`` (crypto.randomUUID).
     */
    hydrateSessionId: () =>
      set((state) => {
        if (state.sessionId && state.sessionId.length > 0) return {};
        // **2026-07-09 fix (sess_836):** restore the previous
        // session from localStorage if one exists. Falling through
        // here means it's a truly cold start (or after a backend
        // wipe) — mint a fresh UUID in that case.
        try {
          if (typeof window !== "undefined") {
            const last = window.localStorage.getItem("tutor:lastSessionId");
            if (last && last.length >= 8) {
              return { sessionId: last };
            }
          }
        } catch {
          // ignore — fall through to minting
        }
        try {
          if (typeof window !== "undefined" && window.crypto) {
            return { sessionId: window.crypto.randomUUID() };
          }
        } catch { /* blocked env */ }
        return { sessionId: `s_${Date.now().toString(36)}` };
      }),

    setSessionId: (sessionId) => {
      // **2026-07-09 fix (sess_836):** persist the active session id
      // to localStorage so a browser refresh (or a backend restart
      // followed by a refresh) lands the user back on the same
      // conversation. Without this, every page load minted a fresh
      // UUID and the user's resource packages / messages vanished.
      if (typeof window !== "undefined") {
        try {
          window.localStorage.setItem("tutor:lastSessionId", sessionId);
        } catch {
          // ignore (private mode / blocked storage)
        }
      }
      set({ sessionId });
    },

    loadConversationIntoStore: async (userId, sessionId) => {
      const { getConversation } = await import("./api");
      const detail = await getConversation(userId, sessionId);
      const messages = (detail.messages || []).map((m) => ({
        id: m.id,
        role: m.role as "user" | "assistant" | "system",
        content: m.content,
        agent: m.capability ?? undefined,
        timestamp: new Date(m.created_at).getTime(),
        metadata: m.metadata ?? {},
      }));
      set({
        sessionId,
        messages,
        activeTurn: newActiveTurn(),
        jobsById: {},
        jobOrder: [],
        latestPackage: null,
        resourceSelection: { packageId: null, selectedResourceId: null },
        plannedPath: null,
        latestAssessment: null,
        latestStrategy: null,
        latestUnderstanding: null,
        latestTutorAnswer: null,
        latestEnrichments: [],
      });
    },

    /**
     * 2026-06-21 plan (stage 4): load a full conversation snapshot
     * (header + messages + jobs + packages) in a single REST call
     * and replace the active view atomically. Background jobs in
     * other sessions are NOT cancelled — they continue running on
     * the server; the user simply doesn't see them until they
     * switch back.
     */
    loadConversationAggregate: async (userId, sessionId) => {
      const { getConversationAggregate, getResourcePackageDetail } = await import("./api");
      const agg = await getConversationAggregate(userId, sessionId);
      const conv = agg.conversation;
      const messages = (conv.messages || []).map((m) => ({
        id: m.id,
        role: m.role as "user" | "assistant" | "system",
        content: m.content,
        agent: m.capability ?? undefined,
        timestamp: new Date(m.created_at).getTime(),
        metadata: m.metadata ?? {},
      }));

      // Hydrate the per-job state from the snapshot. Terminal jobs
      // keep their final events; live ones keep streaming — the
      // job-reducer's snapshot event reuses the same path as the
      // useJobQueue.subscribe hot path.
      //
      // **2026-07-09 fix (sess_836 trace):** the previous version
      // hand-built ``ClientJob`` literals, missing required
      // ``Set<string>``/``string``/``string[]`` fields (``seen_event_ids``,
      // ``text_buffer``, ``thinking_buffer``, ``stage``, ``open_stages``).
      // ``applyStream`` then crashed at ``job.seen_event_ids.has(...)``
      // because ``seen_event_ids`` was ``undefined`` — the line-240
      // ``if (!job)`` guard checked existence but not shape. We now
      // build the canonical ``ClientJob`` via ``createJobState`` then
      // overlay the persisted fields. Defense-in-depth: ``applyStream``
      // also got a shape guard so this can't crash if a future
      // hydration path forgets a field again.
      const jobsById: JobsState["jobsById"] = {};
      const jobOrder: JobsState["jobOrder"] = [];
      for (const j of agg.jobs) {
        const base = createJobState(
          j.job_id,
          j.capability,
          [],
          j.message_preview,
          j.created_at ? Date.parse(j.created_at) : Date.now(),
        );
        const seeded: JobsState["jobsById"][string] = {
          ...base.jobsById[j.job_id],
          status: j.status,
          started_at: j.started_at ? Date.parse(j.started_at) : null,
          finished_at: j.finished_at ? Date.parse(j.finished_at) : null,
          event_count: j.event_count,
          error: j.error
            ? { code: "JOB_ERROR", message: j.error }
            : null,
        };
        jobsById[j.job_id] = seeded;
        jobOrder.push(j.job_id);
      }

      // **2026-07-09 fix (sess_ebb / 38a445a1 trace):** when a job was
      // reaped by ``JobRunner.resume_active_jobs`` on backend restart,
      // the persisted conversation has the user message but NO
      // assistant message and NO workflow timeline — because
      // ``job_terminal`` never fired naturally. The chat panel then
      // appears empty (just the bare "正在调用 Agent…" if the user's
      // stale liveJob sneaks in). Synthesise a local-only "task
      // interrupted" message here so the user sees something useful.
      // This message is NOT persisted to backend — it lives in the
      // Zustand store only and is rebuilt on every conversation
      // switch. That's the right shape: it's a UI hint, not a
      // historical fact.
      const messagesWithInterrupt: ChatMessage[] = [...messages];
      const reapedJobs = (agg.jobs || []).filter(
        (j) =>
          j.status === "failed" &&
          typeof j.error === "string" &&
          (j.error.includes("process restarted") ||
            j.error.includes("timed out")),
      );
      if (reapedJobs.length > 0 && messagesWithInterrupt.length > 0) {
        const lastReap = reapedJobs[0];
        const isTimeout = lastReap.error.includes("timed out");
        const interruptionMsg: ChatMessage = {
          id: `interrupted-${lastReap.job_id}`,
          role: "system",
          content: isTimeout
            ? "任务执行超过 600 秒被系统终止。已生成的部分资源仍保留在右侧面板。"
            : "任务在后端重启时被中断。已生成的部分资源仍保留在右侧面板。",
          timestamp: Date.now(),
          metadata: {
            job_id: lastReap.job_id,
            interrupted: true,
            reason: lastReap.error,
          },
        };
        messagesWithInterrupt.push(interruptionMsg);
      }

      // The right pane shows the latest package summary; load the
      // full payload so ResourceDetail can render. If multiple
      // packages belong to the session, take the most recent.
      let latestPackage: ResourcePackage | null = null;
      if (agg.packages && agg.packages.length > 0) {
        const latestSummary = agg.packages[0];
        try {
          latestPackage = await getResourcePackageDetail(userId, latestSummary.package_id);
        } catch (e) {
          // best-effort: if the full payload is gone, leave the
          // right pane empty and let the user re-trigger.
          console.warn(
            `[store] loadConversationAggregate: failed to load package ${latestSummary.package_id}`,
            e,
          );
        }
      }

      set({
        sessionId,
        messages: messagesWithInterrupt,
        activeTurn: newActiveTurn(),
        jobsById,
        jobOrder,
        latestPackage,
        resourceSelection: latestPackage
          ? {
              packageId: latestPackage.package_id,
              selectedResourceId: latestPackage.resources[0]?.resource_id || null,
            }
          : { packageId: null, selectedResourceId: null },
        plannedPath: null,
        latestAssessment: null,
        latestStrategy: null,
        latestUnderstanding: null,
        latestTutorAnswer: null,
        latestEnrichments: [],
      });
    },

    rehydrateJobFromDetail: (detail) =>
      set((state) => {
        const next = reduceJobEvent(
          { jobsById: state.jobsById, jobOrder: state.jobOrder, messages: state.messages },
          {
            type: "snapshot",
            job: {
              job_id: detail.job_id,
              capability: detail.capability,
              status: detail.status,
              message_preview: detail.message_preview,
              submitted_at: detail.created_at
                ? Date.parse(detail.created_at)
                : Date.now(),
              started_at: detail.started_at
                ? Date.parse(detail.started_at)
                : null,
              finished_at: detail.finished_at
                ? Date.parse(detail.finished_at)
                : null,
              last_seq: detail.events?.length ?? 0,
              events: detail.events ?? [],
              result: (detail.result as any) ?? null,
              error: detail.error,
              event_count: detail.event_count,
            },
          },
        );
        return {
          jobsById: next.jobsById,
          jobOrder: next.jobOrder,
          messages: next.messages,
        };
      }),
  })),
);

// helpers

function cryptoRandom(): string {
  if (typeof window !== "undefined" && window.crypto) {
    return window.crypto.randomUUID();
  }
  // SSR fallback
  return `s_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}`;
}

const USER_ID_KEY = "tutor-user-id";
const LEGACY_USER_ID_KEY = "tutor:user_id";

/**
 * Return the per-browser user id from localStorage, or create and
 * persist one. The id is reused across reloads so the backend sees
 * the same user on every page load instead of a fresh "anonymous"
 * literal that no profile lookup can resolve.
 */
export function getOrCreateUserId(multiUserEnabled = false): string {
  if (!multiUserEnabled) {
    try {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(USER_ID_KEY, "local-user");
        window.localStorage.removeItem(LEGACY_USER_ID_KEY);
      }
    } catch {
      // localStorage blocked; the in-memory identity is still canonical.
    }
    return "local-user";
  }
  if (typeof window === "undefined") return "anonymous";
  try {
    const existing = window.localStorage.getItem(USER_ID_KEY);
    if (existing?.trim()) {
      window.localStorage.removeItem(LEGACY_USER_ID_KEY);
      return existing;
    }
    const legacy = window.localStorage.getItem(LEGACY_USER_ID_KEY);
    window.localStorage.removeItem(LEGACY_USER_ID_KEY);
    if (legacy?.trim()) {
      window.localStorage.setItem(USER_ID_KEY, legacy);
      return legacy;
    }
  } catch {
    // localStorage blocked (private mode etc.) — fall through.
  }
  const fresh = `u_${cryptoRandom().replace(/-/g, "")}`;
  try {
    window.localStorage.setItem(USER_ID_KEY, fresh);
  } catch {
    // ignore
  }
  return fresh;
}
