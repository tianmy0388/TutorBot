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
  LearnerProfileDetail,
  PlannedPath,
  QuestionUnderstanding,
  ResourcePackage,
  StreamEvent,
  StrategyDecision,
  TutoringAnswer,
  EnrichmentSuggestion,
} from "./types";

// ---------------------------------------------------------------------------
// Message (chat history)
// ---------------------------------------------------------------------------

export type MessageRole = "user" | "assistant" | "system" | "agent";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  agent?: string; // for role==="agent" trace events
  content: string;
  stage?: string;
  timestamp: number;
  metadata?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Active turn
// ---------------------------------------------------------------------------

export type TurnPhase =
  | "idle"
  | "connecting"
  | "streaming"
  | "success"
  | "error";

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
// Store
// ---------------------------------------------------------------------------

export interface TutorState {
  // Session
  userId: string;
  sessionId: string;
  language: "zh" | "en";
  messages: ChatMessage[];
  activeTurn: ActiveTurn;

  // Profile
  profile: LearnerProfileDetail | null;
  profileLoaded: boolean;

  // Resources
  latestPackage: ResourcePackage | null;
  resourceSelection: ResourceSelection;

  // Knowledge graph
  currentCourse: string;
  plannedPath: PlannedPath | null;

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

  // ---- actions ----
  setUser: (userId: string) => void;
  setLanguage: (lang: "zh" | "en") => void;
  setCurrentCapability: (cap: string | null) => void;
  setTracePanelOpen: (open: boolean) => void;
  setWsConnected: (connected: boolean) => void;
  setProfile: (p: LearnerProfileDetail | null) => void;
  setLatestPackage: (pkg: ResourcePackage | null) => void;
  selectResource: (resourceId: string | null) => void;
  setPlannedPath: (p: PlannedPath | null) => void;
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
  applyStreamEvent: (ev: StreamEvent) => void;
  completeActiveTurn: (result: Record<string, unknown> | null, error: string | null) => void;
  resetSession: () => void;
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

    profile: null,
    profileLoaded: false,

    latestPackage: null,
    resourceSelection: { packageId: null, selectedResourceId: null },

    currentCourse: "ai_introduction",
    plannedPath: null,

    latestAssessment: null,
    latestStrategy: null,

    latestUnderstanding: null,
    latestTutorAnswer: null,
    latestEnrichments: [],

    tracePanelOpen: true,
    currentCapability: null,
    wsConnected: false,

    // --- simple setters ---
    setUser: (userId) => set({ userId }),
    setLanguage: (language) => set({ language }),
    setCurrentCapability: (cap) => set({ currentCapability: cap }),
    setTracePanelOpen: (open) => set({ tracePanelOpen: open }),
    setWsConnected: (connected) => set({ wsConnected: connected }),
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
        const turn = state.activeTurn;
        if (turn.phase === "idle") return state;
        const events = [...turn.events, ev];
        let { text_buffer, thinking_buffer } = turn;
        if (ev.type === "content") {
          text_buffer = text_buffer + ev.content;
        } else if (ev.type === "thinking") {
          thinking_buffer = thinking_buffer + ev.content;
        }
        return { activeTurn: { ...turn, events, text_buffer, thinking_buffer } };
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
