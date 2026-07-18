import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getConversation: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  setConversationWebSearch: vi.fn(),
  appendConversationMessage: vi.fn(),
  listAppCourses: vi.fn(),
  listKnowledgeBases: vi.fn(),
}));
const queueMocks = vi.hoisted(() => ({
  submit: vi.fn(),
  subscribe: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);
vi.mock("@/hooks/useJobQueue", () => ({
  useJobQueue: () => ({
    submit: queueMocks.submit,
    subscribe: queueMocks.subscribe,
    activeJobs: [],
  }),
}));

import { ChatComposer } from "./ChatComposer";
import { useTutorStore } from "@/lib/store";

beforeEach(() => {
  apiMocks.listAppCourses.mockResolvedValue({ items: [] });
  apiMocks.listKnowledgeBases.mockResolvedValue({ items: [] });
  apiMocks.getConversation.mockRejectedValue({ status: 404 });
  apiMocks.createConversation.mockResolvedValue({
    session_id: "draft-session",
    web_search_enabled: false,
  });
  apiMocks.setConversationWebSearch.mockResolvedValue({
    session_id: "draft-session",
    web_search_enabled: true,
  });
  apiMocks.deleteConversation.mockResolvedValue({
    deleted: true,
    session_id: "draft-session",
  });
  apiMocks.appendConversationMessage.mockResolvedValue({});
  queueMocks.submit.mockResolvedValue({
    job_id: "job-1",
    capability: "tutoring",
  });
  useTutorStore.setState({
    userId: "local-user",
    sessionId: "draft-session",
    currentCapability: "tutoring",
    messages: [],
    webSearchEnabled: true,
    webSearchMutationPending: false,
    webSearchError: null,
    conversationMaterialized: false,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ChatComposer draft web search", () => {
  it("persists the draft choice before the first job and annotates the message", async () => {
    render(<ChatComposer />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "current question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => expect(queueMocks.submit).toHaveBeenCalledTimes(1));
    expect(apiMocks.createConversation).toHaveBeenCalledTimes(1);
    expect(apiMocks.setConversationWebSearch).toHaveBeenCalledWith(
      "local-user",
      "draft-session",
      true,
    );
    expect(apiMocks.setConversationWebSearch.mock.invocationCallOrder[0]).toBeLessThan(
      queueMocks.submit.mock.invocationCallOrder[0],
    );
    expect(apiMocks.appendConversationMessage).toHaveBeenCalledWith(
      "local-user",
      "draft-session",
      expect.objectContaining({
        metadata: expect.objectContaining({ web_search_requested: true }),
      }),
    );
  });

  it("keeps a failed draft retryable and patches before the retry job", async () => {
    apiMocks.setConversationWebSearch
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({
        session_id: "draft-session",
        web_search_enabled: true,
      });
    render(<ChatComposer />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "current question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("设置保存失败"),
    );
    expect(queueMocks.submit).not.toHaveBeenCalled();
    expect(apiMocks.appendConversationMessage).not.toHaveBeenCalled();
    expect(apiMocks.deleteConversation).toHaveBeenCalledWith(
      "local-user",
      "draft-session",
    );
    expect(useTutorStore.getState().conversationMaterialized).toBe(false);
    expect(useTutorStore.getState().webSearchEnabled).toBe(true);
    expect(
      useTutorStore.getState().messages.filter((message) => message.role === "user"),
    ).toHaveLength(0);
    expect(screen.getByRole("textbox")).toHaveValue("current question");

    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => expect(queueMocks.submit).toHaveBeenCalledTimes(1));
    expect(apiMocks.setConversationWebSearch).toHaveBeenCalledTimes(2);
    expect(apiMocks.setConversationWebSearch.mock.invocationCallOrder[1]).toBeLessThan(
      queueMocks.submit.mock.invocationCallOrder[0],
    );
    expect(apiMocks.appendConversationMessage).toHaveBeenCalledTimes(1);
    expect(apiMocks.appendConversationMessage).toHaveBeenCalledWith(
      "local-user",
      "draft-session",
      expect.objectContaining({
        metadata: expect.objectContaining({ web_search_requested: true }),
      }),
    );
    expect(
      useTutorStore.getState().messages.filter((message) => message.role === "user"),
    ).toHaveLength(1);
  });
});
