import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const mocks = vi.hoisted(() => {
  const storeState = {
    userId: "u-test",
    sessionId: "s-test",
    setProfile: vi.fn(),
    setLatestPackage: vi.fn(),
    setPlannedPath: vi.fn(),
    setLatestAssessment: vi.fn(),
    setLatestStrategy: vi.fn(),
    setSessionId: vi.fn(),
  };
  return {
    listDemoScenarios: vi.fn(),
    loadDemoScenario: vi.fn(),
    storeState,
  };
});

vi.mock("@/lib/api", () => ({
  listDemoScenarios: (...args: unknown[]) => mocks.listDemoScenarios(...args),
  loadDemoScenario: (...args: unknown[]) => mocks.loadDemoScenario(...args),
}));

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (s: typeof mocks.storeState) => unknown) =>
    selector(mocks.storeState),
}));

import DemoPage from "./page";

describe("DemoPage", () => {
  afterEach(() => {
    cleanup();
    mocks.listDemoScenarios.mockReset();
    mocks.loadDemoScenario.mockReset();
    Object.values(mocks.storeState).forEach((value) => {
      if (typeof value === "function" && "mockReset" in value) {
        value.mockReset();
      }
    });
  });

  it("loads a competition scenario and renders trace, loop, evidence, and exports", async () => {
    mocks.listDemoScenarios.mockResolvedValueOnce({
      items: [
        {
          id: "ai_intro_competition",
          title: "AI 入门学习闭环",
          course: "ai_introduction",
          topic: "Transformer 与注意力机制",
          description: "比赛演示场景",
          persona: "计算机专业大二学生",
          goal: "两周内理解注意力机制",
          estimated_minutes: 12,
          tags: ["A3"],
          live_prompt: "请生成学习资源",
        },
      ],
    });
    mocks.loadDemoScenario.mockResolvedValueOnce(buildDemoResult());

    render(<DemoPage />);

    expect(await screen.findByTestId("demo-scenario-select")).toHaveValue(
      "ai_intro_competition",
    );
    fireEvent.click(screen.getByTestId("demo-load-seeded"));

    await waitFor(() =>
      expect(mocks.loadDemoScenario).toHaveBeenCalledWith(
        "ai_intro_competition",
        expect.objectContaining({
          user_id: "u-test",
          session_id: "s-test",
          persist: true,
          mode: "seeded",
        }),
      ),
    );

    expect(await screen.findByTestId("demo-agent-timeline")).toHaveTextContent(
      "画像分析",
    );
    expect(screen.getByTestId("demo-loop-goal")).toHaveTextContent("学习目标");
    expect(screen.getByTestId("demo-resource-evidence")).toHaveTextContent(
      "Attention Is All You Need",
    );
    expect(screen.getByTestId("demo-runtime-warnings")).toHaveTextContent(
      "Embedding",
    );
    expect(screen.getByTestId("demo-export-markdown")).not.toBeDisabled();
    expect(screen.getByTestId("demo-export-pdf")).not.toBeDisabled();
    expect(mocks.storeState.setProfile).toHaveBeenCalled();
    expect(mocks.storeState.setLatestPackage).toHaveBeenCalled();
  });
});

function buildDemoResult() {
  const createdAt = "2026-07-15T00:00:00.000Z";
  return {
    scenario: {
      id: "ai_intro_competition",
      title: "AI 入门学习闭环",
      course: "ai_introduction",
      topic: "Transformer 与注意力机制",
      description: "比赛演示场景",
      persona: "计算机专业大二学生",
      goal: "两周内理解注意力机制",
      estimated_minutes: 12,
      tags: ["A3"],
      live_prompt: "请生成学习资源",
    },
    user_id: "u-test",
    session_id: "s-demo",
    profile: {
      user_id: "u-test",
      version: 3,
      cognitive_style: "visual",
      knowledge_count: 2,
      avg_mastery: 0.52,
      weak_concepts: ["attention"],
      strong_concepts: ["ai_basic"],
      error_pattern_count: 1,
      goal: "competition",
      urgency: "high",
      self_efficacy: 0.62,
      modality_dominant: "diagram",
      session_duration_min: 40,
      updated_at: createdAt,
      knowledge_map: { ai_basic: 0.82, attention: 0.34 },
      modality: {
        text: 0.5,
        video: 0.7,
        interactive: 0.8,
        diagram: 0.9,
        code: 0.6,
        audio: 0.2,
        exercise: 0.8,
      },
      pace: {
        avg_session_duration_min: 40,
        preferred_chunk_size_min: 12,
        review_interval_hours: 24,
        daily_time_budget_min: 45,
        sessions_per_week: 5,
      },
      motivation: {
        goal_type: "competition",
        goal_description: "比赛展示",
        urgency: "high",
        self_efficacy: 0.62,
        target_completion_date: null,
        stakes: "答辩",
      },
      error_patterns: [],
      metadata: {},
    },
    path: {
      path_id: "p1",
      course: "ai_introduction",
      name: "冲刺路径",
      description: "先补注意力",
      nodes: [
        {
          id: "ai_basic",
          name: "AI 基础",
          category: "foundation",
          difficulty: 1,
          estimated_hours: 1,
          prerequisites: [],
          status: "completed",
        },
        {
          id: "attention",
          name: "注意力机制",
          category: "core",
          difficulty: 4,
          estimated_hours: 2,
          prerequisites: ["ai_basic"],
          status: "available",
        },
      ],
      total_estimated_hours: 3,
      completed_count: 1,
      available_count: 1,
      locked_count: 0,
      generated_at: createdAt,
    },
    package: {
      package_id: "pkg-demo",
      topic: "Transformer 与注意力机制",
      resources: [
        {
          resource_id: "res-doc",
          type: "document",
          title: "注意力机制讲解",
          content: "content",
          format_specific: {},
          difficulty: 3,
          estimated_minutes: 12,
          prerequisites: [],
          generated_by: ["ContentExpertAgent"],
          confidence_score: 0.91,
          topic: "Transformer 与注意力机制",
          tags: ["attention"],
          created_at: createdAt,
          metadata: {},
          citations: [{ title: "Attention Is All You Need", url: "https://arxiv.org/abs/1706.03762" }],
          review: { verdict: "pass", quality_score: 0.9, issues: [], suggestions: [], reviewer: "QualityReviewerAgent" },
          safety: { verdict: "safe", risk_level: "low" },
          unverified_claims: [],
        },
      ],
      target_profile_snapshot: {},
      learning_path_summary: {},
      generated_by: ["ContentExpertAgent", "QualityReviewerAgent"],
      metadata: {},
      created_at: createdAt,
    },
    assessment: {
      user_id: "u-test",
      dimension_scores: {
        knowledge_mastery: {
          dimension: "knowledge_mastery",
          score: 0.52,
          evidence: [],
          notes: "需要补齐注意力",
        },
      },
      overall_score: 0.64,
      trajectory: "improving",
      weak_concepts: ["attention"],
      strong_concepts: ["ai_basic"],
      recommendations: ["先做 Q/K/V 小测"],
      notes: "趋势向好",
      event_window_hours: 168,
      events_analyzed: 18,
      created_at: createdAt,
    },
    strategy: {
      user_id: "u-test",
      actions: [
        {
          action_type: "recommend_practice",
          target_concept: "attention",
          target_resource_type: "exercise",
          rationale: "公式到直觉有缺口",
          priority: 1,
          metadata: {},
        },
      ],
      overall_directive: "先补注意力直觉",
      notes: "保持短资源块",
      created_at: createdAt,
    },
    agent_trace: [
      {
        id: "trace-profile",
        agent: "CognitiveDiagnosticAgent",
        role: "画像分析",
        stage: "profile",
        status: "succeeded",
        input_summary: "学习记录",
        output_summary: "识别视觉偏好和薄弱点",
        duration_ms: 800,
        confidence: 0.89,
        artifacts: ["profile"],
      },
    ],
    learning_loop: [
      {
        stage: "goal",
        title: "学习目标",
        status: "done",
        summary: "两周内理解注意力机制",
      },
    ],
    teacher_panel: {
      class_snapshot: "演示学生",
      progress_pct: 50,
      risk_level: "medium",
      weak_concepts: ["attention"],
      interventions: ["手算注意力权重"],
      evidence: ["attention 掌握度 0.34"],
    },
    runtime_warnings: ["Embedding API key is not configured."],
    live_prompt: "请生成学习资源",
    loaded_at: createdAt,
  };
}
