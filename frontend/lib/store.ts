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
  JobResultContract,
  MessageRole,
  PlannedPath,
  QuestionUnderstanding,
  ResourcePackage,
  RetrievalScope,
  StreamEvent,
  StrategyDecision,
  StructuredError,
  TutoringAnswer,
  EnrichmentSuggestion,
  SessionOrigin,
  VideoRetryResponse,
} from "./types";
import type { RecoveryWarning } from "./api";
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
  sessionOrigin: SessionOrigin;
  language: "zh" | "en";
  messages: ChatMessage[];
  activeTurn: ActiveTurn;

  // Jobs (Task 3: per-job state, replaces the single activeTurn model)
  jobsById: JobsState["jobsById"];
  jobOrder: JobsState["jobOrder"];

  // Profile
  profile: LearnerProfileDetail | null;
  profileOwnerId: string | null;
  profileLoaded: boolean;

  // Resources
  latestPackage: ResourcePackage | null;
  resourceSelection: ResourceSelection;
  profileSummary: Record<string, unknown>;
  pathSummary: Record<string, unknown>;
  recoveryWarnings: RecoveryWarning[];

  // Knowledge graph
  currentCourse: string;
  plannedPath: PlannedPath | null;
  plannedPathOwnerId: string | null;
  plannedPathLoaded: boolean;

  // Knowledge base (Task 9)
  activeKnowledgeBaseId: string;

  // 2026-06-21 plan (D10): explicit RAG scope.
  // ``retrievalScope`` is the user-picked scope; ``ragEnabled``
  // toggles the whole RAG path. The two together replace the
  // old ``activeKnowledgeBaseId`` field for vector retrieval
  // (which we keep as the legacy fallback for pre-fix
  // capabilities).
  ragEnabled: boolean;
  retrievalScope: RetrievalScope | null;
  webSearchEnabled: boolean;
  webSearchMutationPending: boolean;
  webSearchError: string | null;
  conversationMaterialized: boolean;

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
  setProfile: (p: LearnerProfileDetail | null, ownerId?: string) => void;
  setLatestPackage: (pkg: ResourcePackage | null) => void;
  selectResource: (resourceId: string | null) => void;
  dismissRecoveryWarning: (index: number) => void;
  setPlannedPath: (p: PlannedPath | null, ownerId?: string) => void;
  setActiveKnowledgeBaseId: (id: string) => void;
  // 2026-06-21 plan (D10): RAG scope setters.
  setRagEnabled: (enabled: boolean) => void;
  setRetrievalScope: (scope: RetrievalScope | null) => void;
  setDraftWebSearchEnabled: (enabled: boolean) => void;
  restoreDraftWebSearch: (enabled: boolean) => void;
  setConversationMaterialized: (materialized: boolean) => void;
  setConversationWebSearch: (
    userId: string,
    sessionId: string,
    enabled: boolean,
    options?: { rollbackValue?: boolean },
  ) => Promise<boolean>;
  setLatestAssessment: (a: AssessmentReport | null) => void;
  setLatestStrategy: (s: StrategyDecision | null) => void;
  setTutorResult: (
    understanding: QuestionUnderstanding | null,
    answer: TutoringAnswer | null,
    enrichments: EnrichmentSuggestion[],
  ) => void;

  // Chat actions
  addMessage: (msg: Omit<ChatMessage, "id" | "timestamp">) => void;
  upsertMessage: (message: ChatMessage) => void;
  startActiveTurn: (turnId: string, capability: string) => void;
  applyStreamEvent: (ev: IncomingStreamEvent) => void;
  completeActiveTurn: (result: Record<string, unknown> | null, error: string | null) => void;
  /** Internal: route a typed reducer event through the pure job reducer. */
  applyReducerEvent: (event: import("./job-reducer").ReducerEvent) => void;
  removeJob: (jobId: string) => void;
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
    session_id?: string;
    capability: string;
    status: import("./types").JobStatus;
    message_preview: string;
    created_at?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    events?: import("./types").StreamEvent[];
    event_count?: number;
    result?: JobResultContract | null;
    error?: StructuredError | null;
    children?: import("./types").JobChildSummary[];
    background_status?: import("./types").JobStatus | null;
  }) => void;
  /** Reconcile a durable video retry without reopening historical children. */
  reconcileVideoRetry: (snapshot: VideoRetryResponse) => void;
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

const webSearchMutationRevisionBySession = new Map<string, number>();
const webSearchMutationChainBySession = new Map<string, Promise<unknown>>();
const webSearchConfirmedBySession = new Map<string, boolean>();
const webSearchDesiredBySession = new Map<string, boolean>();
const webSearchPendingSessions = new Set<string>();
let conversationHydrationGeneration = 0;
let conversationHydrationTargetSessionId: string | null = null;

const beginConversationHydration = (sessionId: string): number => {
  conversationHydrationTargetSessionId = sessionId;
  conversationHydrationGeneration += 1;
  return conversationHydrationGeneration;
};

const invalidateConversationHydration = (sessionId: string): void => {
  conversationHydrationTargetSessionId = sessionId;
  conversationHydrationGeneration += 1;
};

const isCurrentConversationHydration = (
  generation: number,
  sessionId: string,
): boolean =>
  generation === conversationHydrationGeneration &&
  sessionId === conversationHydrationTargetSessionId;

const nextWebSearchRevision = (sessionId: string): number => {
  const revision = (webSearchMutationRevisionBySession.get(sessionId) ?? 0) + 1;
  webSearchMutationRevisionBySession.set(sessionId, revision);
  return revision;
};

const currentWebSearchRevision = (sessionId: string): number =>
  webSearchMutationRevisionBySession.get(sessionId) ?? 0;

const webSearchViewForSession = (
  sessionId: string,
  serverValue: boolean,
): { enabled: boolean; pending: boolean } => {
  const pending = webSearchPendingSessions.has(sessionId);
  return {
    enabled: pending
      ? (webSearchDesiredBySession.get(sessionId) ?? serverValue)
      : serverValue,
    pending,
  };
};

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
    sessionOrigin: "none",
    language: "zh",
    messages: [],
    activeTurn: newActiveTurn(),

    jobsById: {},
    jobOrder: [],

    profile: null,
    profileOwnerId: null,
    profileLoaded: false,

    latestPackage: null,
    resourceSelection: { packageId: null, selectedResourceId: null },
    profileSummary: {},
    pathSummary: {},
    recoveryWarnings: [],

    currentCourse: "ai_introduction",
    plannedPath: null,
    plannedPathOwnerId: null,
    plannedPathLoaded: false,
    activeKnowledgeBaseId: "ai_introduction",
    // 2026-06-21 plan (D10): default to RAG enabled, scope = all.
    // The chat composer renders a scope picker that overrides
    // these on a per-turn basis.
    ragEnabled: true,
    retrievalScope: { kind: "all" },
    webSearchEnabled: false,
    webSearchMutationPending: false,
    webSearchError: null,
    conversationMaterialized: false,

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
    setProfile: (p, ownerId) =>
      set((state) => ({
        profile: p,
        profileOwnerId: ownerId ?? state.userId,
        profileLoaded: true,
      })),
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
    setPlannedPath: (p, ownerId) =>
      set((state) => ({
        plannedPath: p,
        plannedPathOwnerId: ownerId ?? state.userId,
        plannedPathLoaded: true,
      })),
    setActiveKnowledgeBaseId: (id) => set({ activeKnowledgeBaseId: id }),
    setRagEnabled: (enabled) => set({ ragEnabled: enabled }),
    setRetrievalScope: (scope) => set({ retrievalScope: scope }),
    setDraftWebSearchEnabled: (enabled) => {
      const sessionId = get().sessionId;
      if (sessionId) {
        nextWebSearchRevision(sessionId);
        webSearchPendingSessions.delete(sessionId);
        webSearchDesiredBySession.set(sessionId, enabled);
        webSearchConfirmedBySession.delete(sessionId);
      }
      set({
        webSearchEnabled: enabled,
        webSearchMutationPending: false,
        webSearchError: null,
      });
    },
    restoreDraftWebSearch: (enabled) => {
      const sessionId = get().sessionId;
      if (sessionId) {
        nextWebSearchRevision(sessionId);
        webSearchPendingSessions.delete(sessionId);
        webSearchDesiredBySession.set(sessionId, enabled);
        webSearchConfirmedBySession.delete(sessionId);
      }
      set({
        webSearchEnabled: enabled,
        webSearchMutationPending: false,
        conversationMaterialized: false,
      });
    },
    setConversationMaterialized: (conversationMaterialized) =>
      set({ conversationMaterialized }),
    setConversationWebSearch: (userId, sessionId, enabled, options) => {
      const explicitRollback = options?.rollbackValue;
      if (explicitRollback !== undefined) {
        webSearchConfirmedBySession.set(sessionId, explicitRollback);
      } else if (!webSearchConfirmedBySession.has(sessionId)) {
        webSearchConfirmedBySession.set(sessionId, get().webSearchEnabled);
      }
      const revision = nextWebSearchRevision(sessionId);
      webSearchDesiredBySession.set(sessionId, enabled);
      webSearchPendingSessions.add(sessionId);
      if (get().sessionId === sessionId) {
        set({
          webSearchEnabled: enabled,
          webSearchMutationPending: true,
          webSearchError: null,
        });
      }

      const previousChain =
        webSearchMutationChainBySession.get(sessionId) ?? Promise.resolve();
      const operation = previousChain.then(async () => {
        try {
          const { setConversationWebSearch } = await import("./api");
          const persisted = await setConversationWebSearch(
            userId,
            sessionId,
            enabled,
          );
          const confirmedValue = Boolean(persisted.web_search_enabled);
          webSearchConfirmedBySession.set(sessionId, confirmedValue);
          if (
            revision === currentWebSearchRevision(sessionId) &&
            get().sessionId === sessionId
          ) {
            set({
              webSearchEnabled: confirmedValue,
              webSearchError: null,
            });
          }
          return true;
        } catch {
          const confirmedValue =
            webSearchConfirmedBySession.get(sessionId) ?? false;
          if (revision === currentWebSearchRevision(sessionId)) {
            webSearchDesiredBySession.set(sessionId, confirmedValue);
          }
          if (
            revision === currentWebSearchRevision(sessionId) &&
            get().sessionId === sessionId
          ) {
            set({
              webSearchEnabled: confirmedValue,
              webSearchError: "设置保存失败，已恢复先前状态",
            });
          }
          return false;
        } finally {
          if (revision === currentWebSearchRevision(sessionId)) {
            webSearchPendingSessions.delete(sessionId);
            if (get().sessionId === sessionId) {
              set({ webSearchMutationPending: false });
            }
          }
        }
      });
      const settled = operation.then(() => undefined);
      webSearchMutationChainBySession.set(sessionId, settled);
      void settled.finally(() => {
        if (webSearchMutationChainBySession.get(sessionId) === settled) {
          webSearchMutationChainBySession.delete(sessionId);
        }
      });
      return operation;
    },
    setLatestAssessment: (a) => set({ latestAssessment: a }),
    setLatestStrategy: (s) => set({ latestStrategy: s }),
    setTutorResult: (understanding, answer, enrichments) =>
      set({
        latestUnderstanding: understanding,
        latestTutorAnswer: answer,
        latestEnrichments: enrichments,
      }),
    dismissRecoveryWarning: (index) =>
      set((state) => ({
        recoveryWarnings: state.recoveryWarnings.filter((_, i) => i !== index),
      })),

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
      set(() => {
        const sessionId = get().sessionId;
        if (sessionId) {
          nextWebSearchRevision(sessionId);
          webSearchPendingSessions.delete(sessionId);
          webSearchDesiredBySession.delete(sessionId);
          webSearchConfirmedBySession.delete(sessionId);
        }
        return {
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
        profileSummary: {},
        pathSummary: {},
        recoveryWarnings: [],
        plannedPath: null,
        plannedPathOwnerId: null,
        plannedPathLoaded: false,
        latestAssessment: null,
        latestStrategy: null,
        latestUnderstanding: null,
        latestTutorAnswer: null,
        latestEnrichments: [],
        webSearchEnabled: false,
        webSearchMutationPending: false,
        webSearchError: null,
        conversationMaterialized: false,
        };
      }),

    upsertMessage: (message) =>
      set((state) => ({
        messages: state.messages.some((existing) => existing.id === message.id)
          ? state.messages.map((existing) => existing.id === message.id ? message : existing)
          : [...state.messages, message],
      })),

    removeJob: (jobId) =>
      set((state) => {
        const hasJob = Object.prototype.hasOwnProperty.call(state.jobsById, jobId);
        const { [jobId]: _removed, ...remainingJobs } = state.jobsById;
        return {
          jobsById: hasJob ? remainingJobs : state.jobsById,
          jobOrder: state.jobOrder.filter((id) => id !== jobId),
        };
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
              return { sessionId: last, sessionOrigin: "restored" };
            }
          }
        } catch {
          // ignore — fall through to minting
        }
        try {
          if (typeof window !== "undefined" && window.crypto) {
            return { sessionId: window.crypto.randomUUID(), sessionOrigin: "draft" };
          }
        } catch { /* blocked env */ }
        return { sessionId: `s_${Date.now().toString(36)}`, sessionOrigin: "draft" };
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
      invalidateConversationHydration(sessionId);
      set({ sessionId, sessionOrigin: "none" });
    },

    loadConversationIntoStore: async (userId, sessionId) => {
      const { getConversation } = await import("./api");
      const detail = await getConversation(userId, sessionId);
      const messages = hydrateConversationMessages(detail.messages || []);
      const serverWebSearch = detail.web_search_enabled ?? false;
      const webSearchView = webSearchViewForSession(sessionId, serverWebSearch);
      if (!webSearchView.pending) {
        webSearchConfirmedBySession.set(sessionId, serverWebSearch);
        webSearchDesiredBySession.set(sessionId, serverWebSearch);
      }
      set({
        sessionId,
        sessionOrigin: "server",
        webSearchEnabled: webSearchView.enabled,
        webSearchMutationPending: webSearchView.pending,
        webSearchError: null,
        conversationMaterialized: true,
        messages,
        activeTurn: newActiveTurn(),
        jobsById: {},
        jobOrder: [],
        latestPackage: null,
        resourceSelection: { packageId: null, selectedResourceId: null },
        profileSummary: {},
        pathSummary: {},
        recoveryWarnings: [],
        plannedPath: null,
        plannedPathOwnerId: null,
        plannedPathLoaded: false,
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
      const hydrationGeneration = beginConversationHydration(sessionId);
      const { getConversationAggregate } = await import("./api");
      let agg;
      try {
        agg = await getConversationAggregate(userId, sessionId);
      } catch (error) {
        if ((error as { status?: number }).status === 404 && isCurrentConversationHydration(hydrationGeneration, sessionId)) {
          set({ sessionOrigin: "draft", conversationMaterialized: false });
          return;
        }
        throw error;
      }
      if (!isCurrentConversationHydration(hydrationGeneration, sessionId)) {
        return;
      }
      const conv = agg.conversation;
      const messages = hydrateConversationMessages(conv.messages || []);

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
          error: j.error ?? null,
          children: j.children ?? [],
          background_status: j.background_status ?? null,
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
      // Full packages are part of the same aggregate response. Packages are
      // in creation order, so the final item is the active right-pane view.
      const latestPackage: ResourcePackage | null = agg.packages?.length
        ? agg.packages[agg.packages.length - 1]
        : null;

      const serverWebSearch = conv.web_search_enabled ?? false;
      const webSearchView = webSearchViewForSession(sessionId, serverWebSearch);
      if (!webSearchView.pending) {
        webSearchConfirmedBySession.set(sessionId, serverWebSearch);
        webSearchDesiredBySession.set(sessionId, serverWebSearch);
      }
      set({
        sessionId,
        sessionOrigin: "server",
        webSearchEnabled: webSearchView.enabled,
        webSearchMutationPending: webSearchView.pending,
        webSearchError: null,
        conversationMaterialized: true,
        messages,
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
        profileSummary: agg.profile_summary ?? {},
        pathSummary: agg.path_summary ?? {},
        recoveryWarnings: agg.recovery_warnings ?? [],
        plannedPath: null,
        plannedPathOwnerId: null,
        plannedPathLoaded: false,
        latestAssessment: null,
        latestStrategy: null,
        latestUnderstanding: null,
        latestTutorAnswer: null,
        latestEnrichments: [],
      });
    },

    rehydrateJobFromDetail: (detail) =>
      set((state) => {
        if (
          detail.session_id &&
          state.sessionId &&
          detail.session_id !== state.sessionId
        ) {
          return {};
        }
        const existing = state.jobsById[detail.job_id];
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
              last_seq:
                detail.events === undefined
                  ? existing?.last_seq ?? 0
                  : Math.max(0, ...detail.events.map((event) => event.seq ?? 0)),
              events: detail.events ?? existing?.events ?? [],
              result: detail.result ?? null,
              error: detail.error,
              event_count: detail.event_count,
              children: detail.children ?? [],
              background_status: detail.background_status ?? null,
            },
          },
        );
        return {
          jobsById: next.jobsById,
          jobOrder: next.jobOrder,
          messages: next.messages,
        };
      }),
    reconcileVideoRetry: (snapshot) =>
      set((state) => {
        const existingParent = state.jobsById[snapshot.parent_job_id];
        const baseParent =
          existingParent ??
          createJobState(
            snapshot.parent_job_id,
            "resource_generation",
            [],
          ).jobsById[snapshot.parent_job_id];
        const children = [
          ...(baseParent.children ?? []).filter(
            (child) => child.job_id !== snapshot.child.job_id,
          ),
          snapshot.child,
        ];
        const parent = {
          ...baseParent,
          status: existingParent?.status ?? ("succeeded" as const),
          children,
          background_status: snapshot.child.status,
        };
        const latestPackage =
          state.latestPackage?.package_id === snapshot.package_id
            ? {
                ...state.latestPackage,
                resources: state.latestPackage.resources.map((resource) =>
                  resource.resource_id === snapshot.resource_id
                    ? snapshot.resource
                    : resource,
                ),
              }
            : state.latestPackage;
        return {
          jobsById: {
            ...state.jobsById,
            [snapshot.parent_job_id]: parent,
          },
          jobOrder: state.jobOrder.includes(snapshot.parent_job_id)
            ? state.jobOrder
            : [snapshot.parent_job_id, ...state.jobOrder],
          latestPackage,
        };
      }),
  })),
);

// helpers

function hydrateConversationMessages(messages: Array<{
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  capability?: string | null;
  created_at: string;
  metadata?: Record<string, unknown>;
}>): ChatMessage[] {
  const hydrated: ChatMessage[] = [];
  const indexById = new Map<string, number>();
  for (const message of messages) {
    const metadata = message.metadata ?? {};
    const workflowJobId =
      metadata.kind === "workflow_timeline" && typeof metadata.job_id === "string"
        ? metadata.job_id
        : null;
    const id = workflowJobId ? `workflow:${workflowJobId}` : message.id;
    const next: ChatMessage = {
      id,
      role: message.role,
      content: message.content,
      agent: message.capability ?? undefined,
      timestamp: new Date(message.created_at).getTime(),
      metadata,
    };
    const existingIndex = indexById.get(id);
    if (existingIndex === undefined) {
      indexById.set(id, hydrated.length);
      hydrated.push(next);
    } else {
      hydrated[existingIndex] = next;
    }
  }
  return hydrated;
}

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
