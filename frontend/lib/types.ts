/**
 * Shared TypeScript types for the Tutor frontend.
 * Mirrors the backend Pydantic schemas in:
 *   - tutor/services/learner_profile/schema.py
 *   - tutor/services/knowledge_graph/schema.py
 *   - tutor/services/resource_package/schema.py
 *   - tutor/services/tutor/...
 *   - tutor/services/learning_events/schema.py
 *   - tutor/agents/...
 */

// ============================================================================
// Chat messages
// ============================================================================

export type MessageRole = "user" | "assistant" | "system" | "agent";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  agent?: string;
  content: string;
  stage?: string;
  timestamp: number;
  metadata?: Record<string, unknown>;
}

// ============================================================================
// Stream events (from StreamBus on backend)
// ============================================================================

export type StreamEventType =
  | "stage_start"
  | "stage_end"
  | "thinking"
  | "observation"
  | "content"
  | "content_final"
  | "resource"
  | "tool_call"
  | "tool_result"
  | "progress"
  | "sources"
  | "result"
  | "error"
  | "cancelled"
  | "session"
  | "done"
  | "job_terminal";

export interface StreamEvent {
  type: StreamEventType;
  source: string;
  stage: string;
  content: string;
  metadata: Record<string, unknown>;
  session_id: string;
  turn_id: string;
  seq: number;
  timestamp: number;
  event_id: string;
}

// ============================================================================
// WebSocket messages
// ============================================================================

export type WSClientMessage =
  | { type: "start_turn"; session_id?: string; user_id?: string; message: string; capability?: string; language?: string; history?: WSHistoryMessage[]; metadata?: Record<string, unknown> }
  | { type: "submit_job"; session_id?: string; user_id?: string; message: string; capability?: string; language?: string; metadata?: Record<string, unknown> }
  | { type: "subscribe_job"; job_id: string }
  | { type: "cancel"; turn_id?: string; job_id?: string; user_id?: string }
  | { type: "ping" };

export interface WSHistoryMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface WSServerMessage {
  // Stream events (type === one of StreamEventType)
  type: StreamEventType | "ack" | "pong" | "job_submitted";
  source?: string;
  stage?: string;
  content?: string;
  metadata?: Record<string, unknown>;
  // Result payload is in metadata
  session_id?: string;
  turn_id?: string;
  seq?: number;
  timestamp?: number;
  event_id?: string;
  // job_submitted fields
  job_id?: string;
  capability?: string;
  status?: JobStatus;
  created_at?: string;
  user_id?: string;
}

// ============================================================================
// Learner profile (matches LearnerProfile Pydantic model)
// ============================================================================

export type CognitiveStyle =
  | "visual"
  | "verbal"
  | "deductive"
  | "inductive"
  | "active"
  | "reflective";

export type GoalType =
  | "exam_prep"
  | "project_build"
  | "skill_upgrade"
  | "curiosity"
  | "research"
  | "competition";

export type Urgency = "low" | "medium" | "high" | "critical";

export interface ErrorPattern {
  concept: string;
  mistake_type: string;
  frequency: number;
  last_observed?: string;
  examples?: string[];
  notes?: string;
}

export interface PaceProfile {
  avg_session_duration_min: number;
  preferred_chunk_size_min: number;
  review_interval_hours: number;
  daily_time_budget_min: number;
  sessions_per_week: number;
}

export interface MotivationProfile {
  goal_type: GoalType;
  goal_description: string;
  urgency: Urgency;
  self_efficacy: number;
  target_completion_date?: string | null;
  stakes: string;
}

export interface ModalityPreferences {
  text: number;
  video: number;
  interactive: number;
  diagram: number;
  code: number;
  audio: number;
  exercise: number;
}

export interface LearnerProfileSummary {
  user_id: string;
  version: number;
  cognitive_style: CognitiveStyle;
  knowledge_count: number;
  avg_mastery: number;
  weak_concepts: string[];
  strong_concepts: string[];
  error_pattern_count: number;
  goal: GoalType;
  urgency: Urgency;
  self_efficacy: number;
  modality_dominant: string;
  session_duration_min: number;
  updated_at: string;
}

export interface LearnerProfileDetail extends LearnerProfileSummary {
  knowledge_map: Record<string, number>;
  modality: ModalityPreferences;
  pace: PaceProfile;
  motivation: MotivationProfile;
  error_patterns: ErrorPattern[];
  metadata: Record<string, unknown>;
}

// ============================================================================
// Knowledge graph
// ============================================================================

export type NodeStatus =
  | "locked"
  | "available"
  | "in_progress"
  | "completed"
  | "skipped";

export type EdgeType = "prerequisite" | "related" | "extends";

export interface KGNodeSummary {
  id: string;
  name: string;
  category: string;
  difficulty: number;
  estimated_hours: number;
  prerequisites: string[];
}

export interface PathStep {
  node_id?: string;
  id?: string;
  name: string;
  category: string;
  difficulty: number;
  status: NodeStatus;
  estimated_hours: number;
  matched_resources: string[];
  prerequisites?: string[];
}

export interface PlannedPath {
  path_id: string;
  course: string;
  name: string;
  description: string;
  nodes: PathStep[];
  total_estimated_hours: number;
  completed_count: number;
  available_count: number;
  locked_count: number;
  generated_at: string;
}

export interface KGNode {
  id: string;
  name: string;
  category: string;
  difficulty: number;
  estimated_hours: number;
  prerequisites: string[];
  learning_outcomes?: string[];
}

export interface KGEdge {
  from: string;
  to: string;
  type: EdgeType;
  weight?: number;
}

export interface CourseGraph {
  course: string;
  version: string;
  description: string;
  nodes: KGNode[];
  edges: KGEdge[];
  stats: { node_count: number; edge_count: number; is_dag: boolean };
}

export interface CourseListResponse {
  courses: string[];
}

// ============================================================================
// Resources
// ============================================================================

export type ResourceType =
  | "document"
  | "mindmap"
  | "exercise"
  | "reading"
  | "video"
  | "code"
  | "ppt";

export interface ReviewVerdict {
  verdict: "pass" | "revise" | "reject";
  quality_score: number;
  issues: string[];
  suggestions: string[];
  reviewer: string;
}

export interface Resource {
  resource_id: string;
  type: ResourceType;
  title: string;
  content: string;
  format_specific: Record<string, unknown>;
  difficulty: number;
  estimated_minutes: number;
  prerequisites: string[];
  generated_by: string[];
  confidence_score: number;
  topic: string;
  tags: string[];
  created_at: string;
  metadata: Record<string, unknown>;
  citations?: Array<Record<string, unknown>>;
  review?: ReviewVerdict | Record<string, unknown>;
  safety?: Record<string, unknown>;
  unverified_claims?: string[];
}

export interface ResourcePackage {
  package_id: string;
  topic: string;
  resources: Resource[];
  summary?: string;
  target_profile_snapshot: LearnerProfileSummary | Record<string, unknown>;
  learning_path_summary: Record<string, unknown>;
  generated_by: string[];
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ResourcePackageSummary {
  package_id: string;
  topic: string;
  resource_count: number;
  total_minutes: number;
  types: string[];
  avg_confidence: number;
  created_at: string;
}

export interface PackageListResponse {
  user_id: string;
  total: number;
  limit: number;
  offset: number;
  items: ResourcePackageSummary[];
}

// ============================================================================
// Resource plan (Task 4)
// ============================================================================

export interface ResourcePlan {
  plan_id: string;
  intent: string;
  topic: string;
  recommended: string[];
  optional: string[];
  estimated_seconds: number;
  rationale: string;
}

export interface ResourcePlanConfirmResponse {
  job_id: string;
  plan_id: string;
  selected_types: string[];
  topic: string;
  estimated_seconds: number;
  status: string;
}

export interface PackageStatsResponse {
  package_count: number;
  resource_count: number;
  total_minutes: number;
  avg_confidence: number;
  topics: string[];
  type_counts: Record<string, number>;
  first_at: string | null;
  last_at: string | null;
}

// ============================================================================
// Jobs (Phase 5.2 + 5.3 contract)
// ============================================================================

export type JobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "cancelled";

export type JobTerminalStatus = "succeeded" | "partial" | "failed" | "cancelled";

export interface JobProgress {
  stage: string;
  percent: number;
  active_agents: string[];
}

export interface JobError {
  code: string;
  message: string;
  diagnostic?: string;
  retryable: boolean;
}

export interface JobWarning {
  code: string;
  message: string;
  resource_type?: string | null;
  context?: Record<string, unknown>;
}

export interface ArtifactResult {
  resource_type: string;
  status: "succeeded" | "failed";
  resource_id?: string | null;
  duration_seconds?: number;
  agents?: string[];
  error?: JobError | null;
  metadata?: Record<string, unknown>;
}

export interface JobResultContract {
  job_id: string;
  capability: string;
  status: JobTerminalStatus;
  assistant_message: string;
  progress?: JobProgress;
  artifacts?: ArtifactResult[];
  /** **2026-07-08 (fdb26152):** resources that streamed to the bus
   * BEFORE the job timed out / failed / was cancelled. The frontend
   * should surface these in the right pane so the user sees the
   * partial result instead of an empty pane. */
  partial_artifacts?: ArtifactResult[];
  warnings?: JobWarning[];
  error?: JobError | null;
  event_cursor?: number;
  finished_at?: string | null;
}

export interface JobSummary {
  job_id: string;
  user_id: string;
  session_id: string;
  capability: string;
  status: JobStatus;
  message_preview: string;
  language: string;
  event_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  has_result: boolean;
  error: string | null;
}

export interface JobDetail extends JobSummary {
  message: string;
  language: string;
  metadata: Record<string, unknown>;
  result: Record<string, unknown> | null;
  events: StreamEvent[];
}

export interface JobListResponse {
  user_id: string;
  total: number;
  limit: number;
  offset: number;
  items: JobSummary[];
}

export interface JobStatsResponse {
  job_count: number;
  active_count: number;
  by_status: Record<string, number>;
  by_capability: Record<string, number>;
  first_at: string | null;
  last_at: string | null;
}

// ============================================================================
// Assessment
// ============================================================================

export type AssessmentDimension =
  | "knowledge_mastery"
  | "engagement"
  | "comprehension"
  | "pace"
  | "gaps"
  | "trajectory";

export type TrajectoryTrend = "improving" | "stagnant" | "declining" | "insufficient_data";

export type ActionType =
  | "recommend_review"
  | "recommend_advance"
  | "recommend_practice"
  | "recommend_tutoring"
  | "recommend_break"
  | "adjust_pace"
  | "no_action";

export interface DimensionScore {
  dimension: AssessmentDimension;
  score: number;
  evidence: string[];
  notes: string;
}

export interface RecommendedAction {
  action_type: ActionType;
  target_concept: string;
  target_resource_type: string;
  rationale: string;
  priority: number;
  metadata: Record<string, unknown>;
}

export interface AssessmentReport {
  user_id: string;
  dimension_scores: Record<AssessmentDimension, DimensionScore>;
  overall_score: number;
  trajectory: TrajectoryTrend;
  weak_concepts: string[];
  strong_concepts: string[];
  recommendations: string[];
  notes: string;
  event_window_hours: number;
  events_analyzed: number;
  created_at: string;
}

export interface StrategyDecision {
  user_id: string;
  actions: RecommendedAction[];
  overall_directive: string;
  notes: string;
  created_at: string;
}

// ============================================================================
// Knowledge base (Task 8 / Task 9)
// ============================================================================

export type IngestionStatus =
  | "uploaded"
  | "extracting"
  | "chunking"
  | "embedding"
  | "ready"
  | "failed";

export interface KnowledgeBaseSummary {
  id: string;
  name: string;
  description: string;
  is_seeded: boolean;
  document_count: number;
  ready_count: number;
  failed_count: number;
  total_chunks: number;
  embedding_model: string;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeBaseDetail extends KnowledgeBaseSummary {
  documents: KnowledgeDocument[];
}

export interface KnowledgeDocument {
  id: string;
  knowledge_base_id: string;
  display_name: string;
  source_filename: string;
  extension: string;
  size_bytes: number;
  checksum: string;
  status: IngestionStatus;
  chunk_count: number;
  embedding_model: string;
  /** Non-fatal embedding warning. The document is still ``ready`` but
      retrieval will be text-only. */
  embedding_warning: string | null;
  error: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeBaseListResponse {
  items: KnowledgeBaseSummary[];
  total: number;
}

// ============================================================================
// Runtime configuration (Task 6)
// ============================================================================

export interface MaskedSecret {
  configured: boolean;
  preview: string;
  /** True if this provider requires an API key to function (false for MCP). */
  required?: boolean;
  /** Optional human hint shown under the field (e.g. for MCP). */
  hint?: string;
}

export interface LLMConfig {
  provider: string;
  model: string;
  base_url: string;
  temperature: number;
  max_tokens: number;
  timeout: number;
  api_key: MaskedSecret;
}

export interface EmbeddingConfig {
  provider: string;
  model: string;
  base_url: string;
  dimensions: number;
  api_key: MaskedSecret;
}

export interface WebSearchConfig {
  enabled: boolean;
  provider: string;
  max_results: number;
  api_key: MaskedSecret;
}

export interface RuntimeConfig {
  llm: LLMConfig;
  embedding: EmbeddingConfig;
  web_search: WebSearchConfig;
}

export interface LLMSectionPatch {
  provider?: string;
  model?: string;
  base_url?: string;
  temperature?: number;
  max_tokens?: number;
  timeout?: number;
  api_key?: string | null;
  clear_api_key?: boolean;
}

export interface EmbeddingSectionPatch {
  provider?: string;
  model?: string;
  base_url?: string;
  dimensions?: number;
  api_key?: string | null;
  clear_api_key?: boolean;
}

export interface WebSearchSectionPatch {
  enabled?: boolean;
  provider?: string;
  max_results?: number;
  api_key?: string | null;
  clear_api_key?: boolean;
}

export interface ConfigTestResult {
  ok: boolean;
  provider: string;
  model?: string;
  dimensions?: number;
  latency_ms: number;
  message: string;
  code?: string;
}

// ============================================================================
// Tutor
// ============================================================================

export type QuestionType =
  | "concept"
  | "method"
  | "debug"
  | "comparison"
  | "practice"
  | "meta"
  | "other";

export type EnrichmentType =
  | "diagram"
  | "code_example"
  | "exercise"
  | "reference"
  | "video";

export interface QuestionUnderstanding {
  question_type: QuestionType;
  concepts: string[];
  difficulty: number;
  student_intent: string;
  follow_up_questions: string[];
  confidence: number;
  raw_question: string;
}

export interface TutoringAnswer {
  tldr: string;
  intuition: string;
  principle: string;
  example: string;
  follow_up_suggestion: string;
  related_concepts: string[];
  full_markdown: string;
  confidence: number;
  sources: string[];
}

export interface EnrichmentSuggestion {
  type: EnrichmentType;
  title: string;
  content: string;
  rationale: string;
  confidence: number;
  metadata: Record<string, unknown>;
}

// ============================================================================
// Generic
// ============================================================================

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  python: string;
}

export interface CapabilitiesResponse {
  capabilities: Array<{
    name: string;
    description: string;
    stages: string[];
    cli_aliases: string[];
  }>;
  tools: Array<{ name: string; description: string }>;
}
