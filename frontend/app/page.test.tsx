import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const listConversations = vi.fn();
const listResourcePackages = vi.fn();
const listAppCourses = vi.fn();
const getProfile = vi.fn();
const setProfile = vi.fn();
let storedProfile: Record<string, unknown> | null = null;

vi.mock("@/lib/api", () => ({
  listConversations: (...args: unknown[]) => listConversations(...args),
  listResourcePackages: (...args: unknown[]) => listResourcePackages(...args),
  listAppCourses: (...args: unknown[]) => listAppCourses(...args),
  getProfile: (...args: unknown[]) => getProfile(...args),
}));

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({
      userId: "student-1",
      currentCourse: "ai_introduction",
      profile: storedProfile,
      plannedPath: null,
      setProfile,
    }),
}));

import LearningHomePage from "./page";

describe("LearningHomePage", () => {
  beforeEach(() => {
    storedProfile = {
      user_id: "student-1",
      weak_concepts: ["链式法则"],
    };
    setProfile.mockReset();
    listConversations.mockReset().mockResolvedValue({
      items: [{
        session_id: "session-1",
        user_id: "student-1",
        title: "理解神经网络的反向传播",
        message_count: 4,
        last_message_preview: "继续看梯度是怎样传回每一层的。",
        created_at: "2026-07-18T08:00:00Z",
        updated_at: "2026-07-18T09:00:00Z",
      }],
      total: 1,
      limit: 3,
      offset: 0,
      has_more: false,
    });
    listResourcePackages.mockReset().mockResolvedValue({
      items: [{
        package_id: "package-1",
        topic: "反向传播复习资料",
        resource_count: 2,
        total_minutes: 18,
        types: ["document", "exercise"],
        avg_confidence: 0.9,
        created_at: "2026-07-18T09:00:00Z",
      }],
      total: 1,
      limit: 3,
      offset: 0,
    });
    listAppCourses.mockReset().mockResolvedValue({
      items: [{ id: "course-1", name: "人工智能导论", description: "课程资料", knowledge_graph_id: "ai_introduction" }],
      total: 1,
    });
    getProfile.mockReset().mockResolvedValue(null);
  });

  afterEach(() => cleanup());

  it("uses persisted learning data for continuation and today's plan", async () => {
    render(<LearningHomePage />);

    expect(await screen.findAllByText("理解神经网络的反向传播")).not.toHaveLength(0);
    expect(screen.getByText("复习：链式法则")).toBeTruthy();
    expect(screen.getAllByText("反向传播复习资料")).not.toHaveLength(0);
    expect(screen.getByText("2 份资料 · 18 分钟")).toBeTruthy();
    expect(getProfile).not.toHaveBeenCalled();
  });

  it("falls back to an honest empty state when services are unavailable", async () => {
    storedProfile = null;
    listConversations.mockRejectedValue(new Error("offline"));
    listResourcePackages.mockRejectedValue(new Error("offline"));
    listAppCourses.mockRejectedValue(new Error("offline"));
    getProfile.mockRejectedValue(new Error("offline"));

    render(<LearningHomePage />);
    await waitFor(() => expect(screen.queryByLabelText("正在同步学习状态")).toBeNull());

    expect(screen.getByText("从一门课程开始")).toBeTruthy();
    expect(screen.getByText("这里还很安静")).toBeTruthy();
    expect(screen.queryByText(/99%|100%|名学生/)).toBeNull();
  });
});
