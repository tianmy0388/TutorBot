import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getConversation: vi.fn(),
  createConversation: vi.fn(),
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

  it("does not submit when first-send setting persistence fails", async () => {
    apiMocks.setConversationWebSearch.mockRejectedValueOnce(new Error("offline"));
    render(<ChatComposer />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "current question" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("设置保存失败"),
    );
    expect(queueMocks.submit).not.toHaveBeenCalled();
  });
});
