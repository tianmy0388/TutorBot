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
import { reduceJobEvent, type JobsState } from "./job-reducer";

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
  setSettingsOpen: (open: boolean) => void;
  setProfile: (p: LearnerProfileDetail | null) => void;
  setLatestPackage: (pkg: ResourcePackage | null) => void;
  selectResource: (resourceId: string | null) => void;
  setPlannedPath: (p: PlannedPath | null) => void;
  setActiveKnowledgeBaseId: (id: string) => void;
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
    userId: "anonymous",
    sessionId: cryptoRandom(),
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
        sessionId: cryptoRandom(),
      }),

    setSessionId: (sessionId) => set({ sessionId }),

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
