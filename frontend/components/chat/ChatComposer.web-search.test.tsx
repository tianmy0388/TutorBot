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
    expect(apiMocks.createConversation).toHaveBeenCalledWith("local-user", {
      session_id: "draft-session",
      title: "current question",
      web_search_enabled: true,
    });
    expect(apiMocks.setConversationWebSearch).not.toHaveBeenCalled();
    expect(apiMocks.createConversation.mock.invocationCallOrder[0]).toBeLessThan(
      queueMocks.submit.mock.invocationCallOrder[0],
    );
    expect(apiMocks.appendConversationMessage).toHaveBeenCalledWith(
      "local-user",
      "draft-session",
      expect.objectContaining({
        metadata: expect.objectContaining({ web_search_requested: true }),
      }),
    );
    expect(queueMocks.submit).toHaveBeenCalledWith(
      "current question",
      "tutoring",
      {
        sessionId: "draft-session",
        webSearchRequested: true,
      },
    );
  });

  it("restores a draft after atomic creation fails and retries without duplicates", async () => {
    apiMocks.createConversation
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
    expect(apiMocks.deleteConversation).not.toHaveBeenCalled();
    expect(useTutorStore.getState().conversationMaterialized).toBe(false);
    expect(useTutorStore.getState().webSearchEnabled).toBe(true);
    expect(
      useTutorStore.getState().messages.filter((message) => message.role === "user"),
    ).toHaveLength(0);
    expect(screen.getByRole("textbox")).toHaveValue("current question");

    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() => expect(queueMocks.submit).toHaveBeenCalledTimes(1));
    expect(apiMocks.createConversation).toHaveBeenCalledTimes(2);
    expect(apiMocks.createConversation.mock.invocationCallOrder[1]).toBeLessThan(
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

  it("never deletes an existing conversation when its settings PATCH fails", async () => {
    apiMocks.getConversation.mockResolvedValue({
      session_id: "draft-session",
      web_search_enabled: false,
      messages: [{ id: "history-1", role: "user", content: "old message" }],
    });
    apiMocks.setConversationWebSearch.mockRejectedValue(new Error("offline"));

    render(<ChatComposer />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "new question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("设置保存失败"),
    );
    expect(apiMocks.deleteConversation).not.toHaveBeenCalled();
    expect(queueMocks.submit).not.toHaveBeenCalled();
    expect(apiMocks.appendConversationMessage).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("new question");
    expect(useTutorStore.getState().conversationMaterialized).toBe(true);
  });

  it("does not restore session A text or settings into session B after a delayed failure", async () => {
    let rejectPatch!: (error: Error) => void;
    apiMocks.getConversation.mockResolvedValue({
      session_id: "draft-session",
      web_search_enabled: false,
      messages: [],
    });
    apiMocks.setConversationWebSearch.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectPatch = reject;
        }),
    );

    render(<ChatComposer />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "question for A" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));
    await waitFor(() => expect(apiMocks.setConversationWebSearch).toHaveBeenCalled());

    useTutorStore.setState({
      sessionId: "session-b",
      conversationMaterialized: true,
      webSearchEnabled: false,
      webSearchMutationPending: false,
    });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "draft for B" },
    });
    rejectPatch(new Error("offline"));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /发送/ })).toBeEnabled(),
    );
    expect(useTutorStore.getState().sessionId).toBe("session-b");
    expect(useTutorStore.getState().conversationMaterialized).toBe(true);
    expect(useTutorStore.getState().webSearchEnabled).toBe(false);
    expect(useTutorStore.getState().webSearchError).toBeNull();
    expect(screen.getByRole("textbox")).toHaveValue("draft for B");
    expect(apiMocks.deleteConversation).not.toHaveBeenCalled();
    expect(queueMocks.submit).not.toHaveBeenCalled();
  });

  it("finishes session A persistence and submission after navigation during draft creation", async () => {
    let resolveCreate!: (conversation: {
      session_id: string;
      web_search_enabled: boolean;
    }) => void;
    apiMocks.createConversation.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );

    render(<ChatComposer />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "question for A" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));
    await waitFor(() => expect(apiMocks.createConversation).toHaveBeenCalled());

    useTutorStore.setState({
      sessionId: "session-b",
      conversationMaterialized: true,
      messages: [],
      webSearchEnabled: false,
    });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "draft for B" },
    });
    resolveCreate({
      session_id: "draft-session",
      web_search_enabled: true,
    });

    await waitFor(() => expect(queueMocks.submit).toHaveBeenCalledTimes(1));
    expect(apiMocks.appendConversationMessage).toHaveBeenCalledWith(
      "local-user",
      "draft-session",
      expect.objectContaining({
        role: "user",
        content: "question for A",
      }),
    );
    expect(queueMocks.submit).toHaveBeenCalledWith(
      "question for A",
      "tutoring",
      {
        sessionId: "draft-session",
        webSearchRequested: true,
      },
    );
    expect(queueMocks.subscribe).toHaveBeenCalledWith(
      "job-1",
      "tutoring",
      { sessionId: "draft-session" },
    );
    expect(useTutorStore.getState().messages).toHaveLength(0);
    expect(screen.getByRole("textbox")).toHaveValue("draft for B");
  });
});
