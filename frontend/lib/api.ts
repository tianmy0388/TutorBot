/**
 * REST API client for Tutor backend.
 *
 * Base URL comes from NEXT_PUBLIC_API_BASE (default: same-origin /api/v1).
 * All requests are JSON; errors throw `ApiError`.
 */

import type {
  AssessmentReport,
  CapabilitiesResponse,
  CourseGraph,
  CourseListResponse,
  HealthResponse,
  JobDetail,
  JobListResponse,
  JobStatsResponse,
  JobSummary,
  LearnerProfileDetail,
  LearnerProfileSummary,
  PackageListResponse,
  PackageStatsResponse,
  PlannedPath,
  Resource,
  ResourcePackage,
  ResourcePackageSummary,
  StrategyDecision,
} from "./types";

const API_BASE =
  (typeof window !== "undefined" && (window as any).__TUTOR_API__) ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "/api/v1";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // ignore
    }
    throw new ApiError(res.status, `${res.status} ${res.statusText}`, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
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
