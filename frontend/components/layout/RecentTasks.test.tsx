import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationSummary } from "@/lib/api";

const pushMock = vi.fn();
const listConversationsMock = vi.fn();
const deleteConversationMock = vi.fn();
const setSessionIdMock = vi.fn();
const resetSessionMock = vi.fn();
const loadConversationAggregateMock = vi.fn();

let pathname = "/";
let currentSessionId = "sess-current";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/lib/api", () => ({
  listConversations: (...args: unknown[]) => listConversationsMock(...args),
  deleteConversation: (...args: unknown[]) => deleteConversationMock(...args),
}));

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: unknown) => unknown) =>
    selector({
      userId: "u1",
      get sessionId() {
        return currentSessionId;
      },
      setSessionId: setSessionIdMock,
      resetSession: resetSessionMock,
      loadConversationAggregate: loadConversationAggregateMock,
    }),
}));

import { RecentTasks } from "./RecentTasks";

function task(sessionId: string, title: string): ConversationSummary {
  return {
    session_id: sessionId,
    user_id: "u1",
    title,
    message_count: 3,
    last_message_preview: title,
    web_search_enabled: false,
    created_at: "2026-07-19T00:00:00Z",
    updated_at: new Date(Date.now() - 30 * 60000).toISOString(),
  };
}

describe("RecentTasks", () => {
  beforeEach(() => {
    pathname = "/";
    currentSessionId = "sess-current";
    listConversationsMock.mockResolvedValue({
      items: [task("sess-a", "学习自注意力"), task("sess-b", "计算机网络")],
      total: 2,
      limit: 8,
      offset: 0,
      has_more: false,
    });
    deleteConversationMock.mockResolvedValue({
      deleted: true,
      session_id: "sess-a",
      packages_deleted: 1,
      jobs_deleted: 1,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("renders rows with title and relative time", async () => {
    render(<RecentTasks />);
    expect(await screen.findByText("学习自注意力")).toBeInTheDocument();
    expect(screen.getByText("计算机网络")).toBeInTheDocument();
    expect(screen.getAllByText("30 分钟前")).toHaveLength(2);
    expect(listConversationsMock).toHaveBeenCalledWith("u1", { limit: 8 });
  });

  it("opens a task: setSessionId + loadConversationAggregate + route", async () => {
    render(<RecentTasks />);
    fireEvent.click(await screen.findByText("学习自注意力"));
    expect(setSessionIdMock).toHaveBeenCalledWith("sess-a");
    expect(loadConversationAggregateMock).toHaveBeenCalledWith("u1", "sess-a");
    // router.push happens after the awaited aggregate load resolves.
    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/workspace");
    });
  });

  it("does not route when already on /workspace", async () => {
    pathname = "/workspace";
    render(<RecentTasks />);
    fireEvent.click(await screen.findByText("学习自注意力"));
    await waitFor(() => {
      expect(loadConversationAggregateMock).toHaveBeenCalled();
    });
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("deletes a non-current task and refreshes the list", async () => {
    render(<RecentTasks />);
    fireEvent.click(
      await screen.findByRole("button", { name: "删除任务 学习自注意力" }),
    );
    expect(deleteConversationMock).toHaveBeenCalledWith("u1", "sess-a");
    // The session reset + list refresh happen after the awaited delete.
    await waitFor(() => {
      expect(listConversationsMock).toHaveBeenCalledTimes(2);
    });
    expect(resetSessionMock).not.toHaveBeenCalled();
  });

  it("deleting the current session resets it like startNewTask", async () => {
    currentSessionId = "sess-a";
    render(<RecentTasks />);
    fireEvent.click(
      await screen.findByRole("button", { name: "删除任务 学习自注意力" }),
    );
    expect(deleteConversationMock).toHaveBeenCalledWith("u1", "sess-a");
    await waitFor(() => {
      expect(resetSessionMock).toHaveBeenCalled();
    });
    expect(setSessionIdMock).toHaveBeenCalledWith(expect.any(String));
    expect(setSessionIdMock.mock.calls[0][0]).not.toBe("sess-a");
  });

  it("aborts the delete when confirm is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<RecentTasks />);
    fireEvent.click(
      await screen.findByRole("button", { name: "删除任务 学习自注意力" }),
    );
    expect(deleteConversationMock).not.toHaveBeenCalled();
  });
});
