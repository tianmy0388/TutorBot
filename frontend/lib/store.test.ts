import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getOrCreateUserId, useTutorStore } from "./store";

describe("getOrCreateUserId", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns the canonical identity in local mode", () => {
    localStorage.setItem("tutor-user-id", "u_stale");
    localStorage.setItem("tutor:user_id", "u_legacy");

    expect(getOrCreateUserId(false)).toBe("local-user");
    expect(localStorage.getItem("tutor-user-id")).toBe("local-user");
    expect(localStorage.getItem("tutor:user_id")).toBeNull();
  });

  it("retains an explicit identity in multi-user mode", () => {
    localStorage.setItem("tutor-user-id", "u_alice");

    expect(getOrCreateUserId(true)).toBe("u_alice");
  });

  it("migrates a legacy identity in multi-user mode", () => {
    localStorage.setItem("tutor:user_id", "u_legacy");

    expect(getOrCreateUserId(true)).toBe("u_legacy");
    expect(localStorage.getItem("tutor-user-id")).toBe("u_legacy");
    expect(localStorage.getItem("tutor:user_id")).toBeNull();
  });

  it("generates a new identity for a blank legacy value", () => {
    localStorage.setItem("tutor:user_id", "   ");

    const identity = getOrCreateUserId(true);

    expect(identity).toMatch(/^u_[a-zA-Z0-9_]+$/);
    expect(localStorage.getItem("tutor-user-id")).toBe(identity);
    expect(localStorage.getItem("tutor:user_id")).toBeNull();
  });
});

describe("per-conversation web search state", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useTutorStore.setState({
      webSearchEnabled: false,
      webSearchMutationPending: false,
      webSearchError: null,
      conversationMaterialized: false,
    });
  });

  it("hydrates each aggregate without bleed and new drafts reset off", async () => {
    const aggregate = (sessionId: string, enabled: boolean) => ({
      conversation: {
        session_id: sessionId,
        user_id: "local-user",
        title: sessionId,
        message_count: 0,
        last_message_preview: "",
        web_search_enabled: enabled,
        created_at: "2026-07-18T00:00:00Z",
        updated_at: "2026-07-18T00:00:00Z",
        messages: [],
      },
      jobs: [],
      packages: [],
      profile_summary: {},
      path_summary: {},
      recovery_warnings: [],
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(aggregate("enabled-session", true)), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(aggregate("disabled-session", false)), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await useTutorStore
      .getState()
      .loadConversationAggregate("local-user", "enabled-session");
    expect(useTutorStore.getState().webSearchEnabled).toBe(true);
    expect(useTutorStore.getState().conversationMaterialized).toBe(true);

    await useTutorStore
      .getState()
      .loadConversationAggregate("local-user", "disabled-session");
    expect(useTutorStore.getState().webSearchEnabled).toBe(false);

    useTutorStore.getState().resetSession();
    expect(useTutorStore.getState().webSearchEnabled).toBe(false);
    expect(useTutorStore.getState().conversationMaterialized).toBe(false);
  });

  it("serializes rapid optimistic mutations so the last server value wins", async () => {
    const resolvers: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(
      () =>
        new Promise<Response>((resolve) => {
          resolvers.push(resolve);
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    useTutorStore.setState({
      sessionId: "session-1",
      webSearchEnabled: false,
      conversationMaterialized: true,
    });

    const first = useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "session-1", true);
    const second = useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "session-1", false);

    expect(useTutorStore.getState().webSearchEnabled).toBe(false);
    expect(useTutorStore.getState().webSearchMutationPending).toBe(true);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    resolvers[0](
      new Response(JSON.stringify({ web_search_enabled: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    resolvers[1](
      new Response(JSON.stringify({ web_search_enabled: false }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await Promise.all([first, second]);
    expect(
      fetchMock.mock.calls.map(([, init]) =>
        JSON.parse((init as RequestInit).body as string),
      ),
    ).toEqual([
      { web_search_enabled: true },
      { web_search_enabled: false },
    ]);
    expect(useTutorStore.getState().webSearchEnabled).toBe(false);
    expect(useTutorStore.getState().webSearchMutationPending).toBe(false);
    expect(useTutorStore.getState().webSearchError).toBeNull();
  });

  it("rolls back the exact prior value and exposes a visible error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    useTutorStore.setState({
      sessionId: "session-1",
      webSearchEnabled: false,
      conversationMaterialized: true,
    });

    const persisted = await useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "session-1", true);

    expect(persisted).toBe(false);
    expect(useTutorStore.getState().webSearchEnabled).toBe(false);
    expect(useTutorStore.getState().webSearchMutationPending).toBe(false);
    expect(useTutorStore.getState().webSearchError).toContain("恢复");
  });

  it("can roll a draft PATCH back to the known server value", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    useTutorStore.setState({
      sessionId: "draft-session",
      webSearchEnabled: true,
      conversationMaterialized: true,
    });

    const persisted = await useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "draft-session", true, {
        rollbackValue: false,
      });

    expect(persisted).toBe(false);
    expect(useTutorStore.getState().webSearchEnabled).toBe(false);
    expect(useTutorStore.getState().webSearchMutationPending).toBe(false);
    expect(useTutorStore.getState().webSearchError).toContain("恢复");
  });

  it("rolls two failed rapid mutations back to the confirmed server value", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    useTutorStore.setState({
      sessionId: "double-failure",
      webSearchEnabled: false,
      conversationMaterialized: true,
    });

    const first = useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "double-failure", true);
    const second = useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "double-failure", false);

    await expect(Promise.all([first, second])).resolves.toEqual([false, false]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useTutorStore.getState().webSearchEnabled).toBe(false);
    expect(useTutorStore.getState().webSearchMutationPending).toBe(false);
    expect(useTutorStore.getState().webSearchError).toContain("恢复");
  });

  it("rolls a failed second mutation back to the first confirmed success", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ web_search_enabled: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockRejectedValueOnce(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    useTutorStore.setState({
      sessionId: "success-then-failure",
      webSearchEnabled: false,
      conversationMaterialized: true,
    });

    const first = useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "success-then-failure", true);
    const second = useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "success-then-failure", false);

    await expect(Promise.all([first, second])).resolves.toEqual([true, false]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useTutorStore.getState().webSearchEnabled).toBe(true);
    expect(useTutorStore.getState().webSearchMutationPending).toBe(false);
    expect(useTutorStore.getState().webSearchError).toContain("恢复");
  });

  it("reconciles a pending session after switching away and hydrating it again", async () => {
    let resolvePatch!: (response: Response) => void;
    const aggregate = (sessionId: string, enabled: boolean) => ({
      conversation: {
        session_id: sessionId,
        user_id: "local-user",
        title: sessionId,
        message_count: 0,
        last_message_preview: "",
        web_search_enabled: enabled,
        created_at: "2026-07-18T00:00:00Z",
        updated_at: "2026-07-18T00:00:00Z",
        messages: [],
      },
      jobs: [],
      packages: [],
      profile_summary: {},
      path_summary: {},
      recovery_warnings: [],
    });
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (init?.method === "PATCH") {
          return new Promise((resolve) => {
            resolvePatch = resolve;
          });
        }
        const sessionId = url.includes("session-b") ? "session-b" : "session-a";
        return Promise.resolve(
          new Response(JSON.stringify(aggregate(sessionId, false)), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    useTutorStore.setState({
      sessionId: "session-a",
      webSearchEnabled: false,
      conversationMaterialized: true,
    });

    const pending = useTutorStore
      .getState()
      .setConversationWebSearch("local-user", "session-a", true);
    await vi.waitFor(() => expect(resolvePatch).toBeTypeOf("function"));
    await useTutorStore
      .getState()
      .loadConversationAggregate("local-user", "session-b");
    await useTutorStore
      .getState()
      .loadConversationAggregate("local-user", "session-a");

    expect(useTutorStore.getState().webSearchEnabled).toBe(true);
    expect(useTutorStore.getState().webSearchMutationPending).toBe(true);

    resolvePatch(
      new Response(JSON.stringify({ web_search_enabled: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(pending).resolves.toBe(true);

    expect(useTutorStore.getState().sessionId).toBe("session-a");
    expect(useTutorStore.getState().webSearchEnabled).toBe(true);
    expect(useTutorStore.getState().webSearchMutationPending).toBe(false);
    expect(useTutorStore.getState().webSearchError).toBeNull();
  });

  it("ignores an older aggregate that resolves after the latest target session", async () => {
    const resolvers = new Map<string, (response: Response) => void>();
    const aggregate = (sessionId: string, enabled: boolean) => ({
      conversation: {
        session_id: sessionId,
        user_id: "local-user",
        title: sessionId,
        message_count: 1,
        last_message_preview: `message from ${sessionId}`,
        web_search_enabled: enabled,
        created_at: "2026-07-18T00:00:00Z",
        updated_at: "2026-07-18T00:00:00Z",
        messages: [
          {
            id: `message-${sessionId}`,
            role: "assistant",
            content: `message from ${sessionId}`,
            capability: null,
            metadata: {},
            created_at: "2026-07-18T00:00:00Z",
          },
        ],
      },
      jobs: [],
      packages: [],
      profile_summary: { session: sessionId },
      path_summary: {},
      recovery_warnings: [],
    });
    const fetchMock = vi.fn(
      (input: RequestInfo | URL): Promise<Response> => {
        const sessionId = String(input).includes("session-b")
          ? "session-b"
          : "session-a";
        return new Promise((resolve) => {
          resolvers.set(sessionId, resolve);
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    useTutorStore.getState().setSessionId("session-a");
    const loadA = useTutorStore
      .getState()
      .loadConversationAggregate("local-user", "session-a");
    useTutorStore.getState().setSessionId("session-b");
    const loadB = useTutorStore
      .getState()
      .loadConversationAggregate("local-user", "session-b");
    await vi.waitFor(() => expect(resolvers.size).toBe(2));

    resolvers.get("session-b")!(
      new Response(JSON.stringify(aggregate("session-b", true)), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await loadB;
    expect(useTutorStore.getState().sessionId).toBe("session-b");
    expect(useTutorStore.getState().messages[0]?.content).toBe(
      "message from session-b",
    );
    expect(useTutorStore.getState().webSearchEnabled).toBe(true);

    resolvers.get("session-a")!(
      new Response(JSON.stringify(aggregate("session-a", false)), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await loadA;

    expect(useTutorStore.getState().sessionId).toBe("session-b");
    expect(useTutorStore.getState().messages[0]?.content).toBe(
      "message from session-b",
    );
    expect(useTutorStore.getState().webSearchEnabled).toBe(true);
    expect(useTutorStore.getState().profileSummary).toEqual({
      session: "session-b",
    });
  });
});
