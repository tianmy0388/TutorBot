/**
 * REST API client for Tutor backend.
 *
 * Base URL comes from NEXT_PUBLIC_API_BASE (default: same-origin /api/v1).
 * All requests are JSON; errors throw `ApiError`.
 */

import type {
  AssessmentReport,
  CapabilitiesResponse,
  ConfigTestResult,
  CourseGraph,
  CourseListResponse,
  EmbeddingSectionPatch,
  HealthResponse,
  JobDetail,
  JobListResponse,
  JobStatsResponse,
  JobSummary,
  KnowledgeBaseDetail,
  KnowledgeBaseListResponse,
  KnowledgeBaseSummary,
  KnowledgeDocument,
  LLMSectionPatch,
  LearnerProfileDetail,
  LearnerProfileSummary,
  PackageListResponse,
  PackageStatsResponse,
  PlannedPath,
  Resource,
  ResourcePackage,
  ResourcePackageSummary,
  ResourcePlan,
  ResourcePlanConfirmResponse,
  RuntimeConfig,
  StrategyDecision,
  WebSearchSectionPatch,
} from "./types";

const API_BASE =
  (typeof window !== "undefined" && (window as any).__TUTOR_API__) ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "/api/v1";

export class ApiError extends Error {
  status: number;
  body: unknown;
  /**
   * The backend's structured error code (e.g. ``EMPTY_DOCUMENT``).
   * Comes from ``body.detail.code`` when FastAPI returns a detail dict.
   */
  code: string | null;
  /** The backend's human-readable detail, when present. */
  detail: string | null;
  /** A per-request correlation id from the backend, when present. */
  requestId: string | null;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === "object") {
      const d = detail as Record<string, unknown>;
      this.code = typeof d.code === "string" ? d.code : null;
      this.detail = typeof d.message === "string" ? d.message : null;
      this.requestId =
        typeof d.request_id === "string" ? d.request_id : null;
    } else {
      this.code = null;
      this.detail = typeof detail === "string" ? detail : null;
      this.requestId = null;
    }
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  // Build a safe header set: caller-supplied headers win, but we never
  // inject a JSON Content-Type when the body is FormData (the browser
  // must set the multipart boundary itself).
  const baseHeaders: Record<string, string> = {};
  const body = init?.body;
  if (!(body instanceof FormData) && body !== undefined && body !== null) {
    baseHeaders["Content-Type"] = "application/json";
  }
  const headers: Record<string, string> = {
    ...baseHeaders,
    ...(init?.headers as Record<string, string> | undefined),
  };
  // If the caller explicitly set Content-Type, honour it.
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    let body: unknown = null;
    let text: string | null = null;
    try {
      text = await res.text();
    } catch {
      // ignore
    }
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = { detail: text };
      }
    }
    throw new ApiError(res.status, `${res.status} ${res.statusText}`, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * Type guard for AbortError so call sites can check without TS warnings.
 */
function isAbortError(e: unknown): boolean {
  return (
    typeof e === "object" &&
    e !== null &&
    "name" in e &&
    (e as { name: string }).name === "AbortError"
  );
}

// ---------------------------------------------------------------------------
// Health / capabilities
// ---------------------------------------------------------------------------

export const getHealth = () => request<HealthResponse>("/health");

export const getCapabilities = () => request<CapabilitiesResponse>("/capabilities");

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

export const getProfileSummary = (userId: string) =>
  request<LearnerProfileSummary>(
    `/kg/ai_introduction/recommend-next?user_id=${encodeURIComponent(userId)}&limit=1`,
  ).catch(() => null); // graceful fallback

export const getProfile = (userId: string) =>
  request<LearnerProfileDetail>(
    `/profile/${encodeURIComponent(userId)}`,
  ).catch((e) => {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  });

// ---------------------------------------------------------------------------
// Knowledge graph
// ---------------------------------------------------------------------------

export const listCourses = () => request<CourseListResponse>("/kg/courses");

// 2026-06-21 plan: Courses API (Part D). Distinct from /kg/courses
// which returns course names from the knowledge graph YAML — this
// endpoint returns the persistent Course rows with aggregate counts.
export interface CourseResponse {
  id: string;
  name: string;
  description: string;
  knowledge_graph_id: string;
  is_seeded: boolean;
  library_count: number;
  document_count: number;
  ready_count: number;
  total_chunks: number;
  created_at: string;
  updated_at: string;
}

export interface CourseListResponseV2 {
  items: CourseResponse[];
  total: number;
}

export const listAppCourses = () =>
  request<CourseListResponseV2>("/courses");

export const getCourseGraph = (course: string) =>
  request<CourseGraph>(`/kg/${encodeURIComponent(course)}`);

export const listCoursePaths = (course: string) =>
  request<{
    course: string;
    paths: Array<{
      id: string;
      name: string;
      description: string;
      sequence: string[];
    }>;
  }>(`/kg/${encodeURIComponent(course)}/paths`);

export const planPath = (
  course: string,
  profile: LearnerProfileDetail,
  pathId = "",
) =>
  request<PlannedPath>(`/kg/${encodeURIComponent(course)}/plan`, {
    method: "POST",
    body: JSON.stringify({ profile, path_id: pathId, course }),
  });

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

export const getResourcesInfo = () =>
  request<{
    name: string;
    version: string;
    supported_types: string[];
    entry_point: string;
    pipeline_stages: string[];
    agents: string[];
  }>("/resources/info");

export const listResourceTypes = () =>
  request<{
    types: Array<{
      id: string;
      name: string;
      agent: string;
    }>;
  }>("/resources/types");

// ResourcePackage is delivered via the WebSocket; persistence-backed
// history endpoints (Phase 5) supplement the live stream.

export const fetchResourcePackage = (packageId: string) =>
  request<ResourcePackage | null>(
    `/resources/packages/${encodeURIComponent(packageId)}`,
  ).catch(() => null);

export const listResourcePackages = (
  userId: string,
  opts: { limit?: number; offset?: number; sinceHours?: number; topic?: string } = {},
) => {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  if (opts.sinceHours) params.set("since_hours", String(opts.sinceHours));
  if (opts.topic) params.set("topic", opts.topic);
  const qs = params.toString();
  return request<PackageListResponse>(
    `/resources/packages/${encodeURIComponent(userId)}${qs ? `?${qs}` : ""}`,
  );
};

export const getResourcePackageDetail = (userId: string, packageId: string) =>
  request<ResourcePackage>(
    `/resources/packages/${encodeURIComponent(userId)}/${encodeURIComponent(packageId)}`,
  );

export const getResourcePackageStats = (userId: string) =>
  request<PackageStatsResponse>(
    `/resources/packages/${encodeURIComponent(userId)}/stats`,
  );

export const deleteResourcePackage = (userId: string, packageId: string) =>
  request<{ deleted: boolean; package_id: string }>(
    `/resources/packages/${encodeURIComponent(userId)}/${encodeURIComponent(packageId)}`,
    { method: "DELETE" },
  );

// ---------------------------------------------------------------------------
// Jobs (Phase 5.2)
// ---------------------------------------------------------------------------

export const listJobs = (
  userId: string,
  opts: { status?: string; limit?: number; offset?: number } = {},
) => {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  const qs = params.toString();
  return request<JobListResponse>(
    `/jobs/${encodeURIComponent(userId)}${qs ? `?${qs}` : ""}`,
  );
};

export const getJobStats = (userId: string) =>
  request<JobStatsResponse>(`/jobs/${encodeURIComponent(userId)}/stats`);

export const getJobDetail = (userId: string, jobId: string) =>
  request<JobDetail>(
    `/jobs/${encodeURIComponent(userId)}/${encodeURIComponent(jobId)}`,
  );

export const cancelJob = (userId: string, jobId: string) =>
  request<{ cancelled: boolean; job_id: string }>(
    `/jobs/${encodeURIComponent(userId)}/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" },
  );

export const deleteJob = (userId: string, jobId: string) =>
  request<{ deleted: boolean; job_id: string }>(
    `/jobs/${encodeURIComponent(userId)}/${encodeURIComponent(jobId)}`,
    { method: "DELETE" },
  );

// ---------------------------------------------------------------------------
// Runtime configuration (Task 6 / Task 7)
// ---------------------------------------------------------------------------

export const getRuntimeConfig = () => request<RuntimeConfig>("/config");

export const updateLLMConfig = (patch: LLMSectionPatch) =>
  request<RuntimeConfig>("/config/llm", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const updateEmbeddingConfig = (patch: EmbeddingSectionPatch) =>
  request<RuntimeConfig>("/config/embedding", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const updateWebSearchConfig = (patch: WebSearchSectionPatch) =>
  request<RuntimeConfig>("/config/web-search", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const testLLMConnection = () =>
  request<ConfigTestResult>("/config/test/llm", { method: "POST" });

export const testEmbeddingConnection = () =>
  request<ConfigTestResult>("/config/test/embedding", { method: "POST" });

export const testWebSearchConnection = () =>
  request<ConfigTestResult>("/config/test/web-search", { method: "POST" });

// ---------------------------------------------------------------------------
// Knowledge bases (Task 8 / Task 9)
// ---------------------------------------------------------------------------

export const listKnowledgeBases = (init?: RequestInit) =>
  request<KnowledgeBaseListResponse>("/knowledge-bases", init);

export const getKnowledgeBase = (id: string, init?: RequestInit) =>
  request<KnowledgeBaseDetail>(
    `/knowledge-bases/${encodeURIComponent(id)}`,
    init,
  );

export const createKnowledgeBase = (name: string, description: string) =>
  request<KnowledgeBaseSummary>("/knowledge-bases", {
    method: "POST",
    body: JSON.stringify({ name, description }),
  });

export const deleteKnowledgeBase = (id: string) =>
  request<{ deleted: boolean; id: string }>(
    `/knowledge-bases/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );

export const uploadKnowledgeDocument = (libId: string, file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<KnowledgeDocument>(
    `/knowledge-bases/${encodeURIComponent(libId)}/documents`,
    { method: "POST", body: form },
  );
};

export const retryKnowledgeDocument = (libId: string, docId: string) =>
  request<KnowledgeDocument>(
    `/knowledge-bases/${encodeURIComponent(libId)}/documents/${encodeURIComponent(docId)}/retry`,
    { method: "POST" },
  );

export const deleteKnowledgeDocument = (libId: string, docId: string) =>
  request<{ deleted: boolean; id: string }>(
    `/knowledge-bases/${encodeURIComponent(libId)}/documents/${encodeURIComponent(docId)}`,
    { method: "DELETE" },
  );

// ---------------------------------------------------------------------------
// Plans (Task 4)
// ---------------------------------------------------------------------------

export const createPlan = (req: {
  message: string;
  user_id?: string;
  language?: string;
}) =>
  request<ResourcePlan>("/plans", {
    method: "POST",
    body: JSON.stringify(req),
  });

export const confirmPlan = (
  planId: string,
  selectedTypes: string[],
  metadata: Record<string, unknown> = {},
) =>
  request<ResourcePlanConfirmResponse>(
    `/plans/${encodeURIComponent(planId)}/confirm`,
    {
      method: "POST",
      body: JSON.stringify({
        selected_types: { types: selectedTypes },
        metadata,
      }),
    },
  );

export const retryJob = (userId: string, jobId: string, resourceTypes: string[]) =>
  request<{
    job_id: string;
    parent_job_id: string;
    selected_types: string[];
    preserved_artifacts: string[];
    topic: string;
    status: string;
  }>(
    `/jobs/${encodeURIComponent(userId)}/${encodeURIComponent(jobId)}/retry`,
    {
      method: "POST",
      body: JSON.stringify({ resource_types: resourceTypes }),
    },
  );

// ---------------------------------------------------------------------------
// Re-exports
// ---------------------------------------------------------------------------

export type {
  AssessmentReport,
  CapabilitiesResponse,
  CourseGraph,
  CourseListResponse,
  HealthResponse,
  LearnerProfileDetail,
  LearnerProfileSummary,
  PlannedPath,
  Resource,
  ResourcePackage,
  ResourcePackageSummary,
  StrategyDecision,
};

// ---------------------------------------------------------------------------
// Conversations (2026-06-21 plan, stage 4)
// ---------------------------------------------------------------------------

export interface ConversationSummary {
  session_id: string;
  user_id: string;
  title: string;
  message_count: number;
  last_message_preview: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  job_id: string | null;
  capability: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface ConversationListResponse {
  items: ConversationSummary[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export const listConversations = (
  userId: string,
  opts: { limit?: number; offset?: number } = {},
) => {
  const params = new URLSearchParams();
  params.set("user_id", userId);
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  return request<ConversationListResponse>(
    `/conversations?${params.toString()}`,
  );
};

export const getConversation = (userId: string, sessionId: string) =>
  request<ConversationDetail>(
    `/conversations/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`,
  );

/**
 * 2026-06-21 plan: aggregate snapshot for one conversation. Returns
 * the conversation header + messages, plus the jobs and resource
 * package summaries that belong to this session, in a single request
 * so the front-end can replace the current view atomically.
 */
export interface ConversationAggregate {
  conversation: ConversationDetail;
  jobs: JobSummary[];
  packages: ResourcePackage[];
  profile_summary: Record<string, unknown>;
  path_summary: Record<string, unknown>;
  recovery_warnings: RecoveryWarning[];
}

export interface RecoveryWarning {
  code:
    | "migrated_ownership"
    | "interrupted_job_repaired"
    | "missing_artifact";
  message: string;
  job_id?: string | null;
  package_id?: string | null;
  resource_id?: string | null;
  artifact_key?: string | null;
}

export const getConversationAggregate = (
  userId: string,
  sessionId: string,
) =>
  request<ConversationAggregate>(
    `/conversations/${encodeURIComponent(sessionId)}/aggregate?user_id=${encodeURIComponent(userId)}`,
  );

export const createConversation = (
  userId: string,
  opts: { session_id?: string; title?: string } = {},
) =>
  request<ConversationSummary>("/conversations", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, ...opts }),
  });

export const renameConversation = (
  userId: string,
  sessionId: string,
  title: string,
) =>
  request<ConversationSummary>(
    `/conversations/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ title }),
    },
  );

export const deleteConversation = (userId: string, sessionId: string) =>
  request<{ deleted: boolean; session_id: string }>(
    `/conversations/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`,
    { method: "DELETE" },
  );

export const appendConversationMessage = (
  userId: string,
  sessionId: string,
  msg: {
    role: "user" | "assistant" | "system";
    content: string;
    job_id?: string | null;
    capability?: string | null;
    metadata?: Record<string, unknown>;
  },
) =>
  request<ConversationMessage>(
    `/conversations/${encodeURIComponent(sessionId)}/messages?user_id=${encodeURIComponent(userId)}`,
    { method: "POST", body: JSON.stringify(msg) },
  );
