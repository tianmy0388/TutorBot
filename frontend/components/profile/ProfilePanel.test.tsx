import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useProfile } from "@/hooks/useProfile";
import { ProfilePanel } from "./ProfilePanel";

vi.mock("@/hooks/useProfile", () => ({ useProfile: vi.fn() }));
vi.mock("recharts", () => {
  const Chart = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    Radar: Chart,
    RadarChart: Chart,
    PolarGrid: Chart,
    PolarAngleAxis: Chart,
    PolarRadiusAxis: Chart,
    ResponsiveContainer: Chart,
    BarChart: Chart,
    Bar: Chart,
    XAxis: Chart,
    YAxis: Chart,
    CartesianGrid: Chart,
    Tooltip: Chart,
    Cell: Chart,
  };
});

const profileHook = vi.mocked(useProfile);
const refresh = vi.fn(async () => undefined);
const cachedProfile = {
  user_id: "local-user",
  version: 2,
  cognitive_style: "visual" as const,
  knowledge_count: 1,
  avg_mastery: 0.7,
  weak_concepts: [],
  strong_concepts: [],
  error_pattern_count: 0,
  goal: "curiosity" as const,
  urgency: "medium" as const,
  self_efficacy: 0.5,
  modality_dominant: "video",
  session_duration_min: 30,
  updated_at: new Date().toISOString(),
  knowledge_map: { attention: 0.7 },
  modality: { text: 0.5, video: 0.8, interactive: 0.5, diagram: 0.5, code: 0.5, audio: 0.2, exercise: 0.7 },
  pace: { avg_session_duration_min: 30, preferred_chunk_size_min: 15, review_interval_hours: 24, daily_time_budget_min: 60, sessions_per_week: 5 },
  motivation: { goal_type: "curiosity" as const, goal_description: "", urgency: "medium" as const, self_efficacy: 0.5, stakes: "" },
  error_patterns: [],
  metadata: {},
};

beforeEach(() => {
  profileHook.mockReset();
  refresh.mockClear();
});
afterEach(cleanup);

it("shows loading without simultaneously claiming the profile is empty", () => {
  profileHook.mockReturnValue({
    profile: null,
    loaded: false,
    loading: true,
    error: null,
    status: "loading",
    refresh,
  });

  render(<ProfilePanel />);

  expect(screen.getByText("学习状态加载中…")).toBeInTheDocument();
  expect(screen.queryByText("暂无学习状态")).not.toBeInTheDocument();
});

it("describes profile readiness in student-facing language", () => {
  profileHook.mockReturnValue({
    profile: null,
    loaded: true,
    loading: false,
    error: null,
    status: "empty",
    refresh,
  });

  render(<ProfilePanel />);

  expect(
    screen.getByText("完成一次学习任务后，这里会逐步整理你的学习状态"),
  ).toBeInTheDocument();
});

it("shows an explicit failure instead of cached profile content after refresh fails", () => {
  profileHook.mockReturnValue({
    profile: cachedProfile,
    loaded: true,
    loading: false,
    error: "refresh offline",
    status: "failed",
    refresh,
  });

  render(<ProfilePanel />);

  expect(screen.getByText("学习状态加载失败")).toBeInTheDocument();
  expect(screen.getByText("refresh offline")).toBeInTheDocument();
});

it("shows major and level from profile metadata", () => {
  profileHook.mockReturnValue({
    profile: {
      ...cachedProfile,
      metadata: { major: "计算机科学", level: "graduate" },
    },
    loaded: true,
    loading: false,
    error: null,
    status: "success",
    refresh,
  });

  render(<ProfilePanel />);

  expect(screen.getByText("专业与层次")).toBeInTheDocument();
  expect(screen.getByText(/计算机科学 · 硕士/)).toBeInTheDocument();
});
