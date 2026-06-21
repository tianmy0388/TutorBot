/**
 * Stage 0 — request protocol regression tests.
 *
 * Pins down the bug from the plan: the request helper forces
 * `Content-Type: application/json` for every request, but upload and
 * knowledge-base-create calls send FormData. The browser will set the
 * correct Content-Type for FormData only if the helper does not
 * override it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.resetModules();
  fetchMock.mockReset();
  // Default: a successful JSON response.
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request() Content-Type", () => {
  it("does NOT set Content-Type when body is FormData", async () => {
    const { createKnowledgeBase } = await import("./api");
    try {
      await createKnowledgeBase("name", "desc");
    } catch {
      // The exact body shape doesn't matter; the headers do.
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/knowledge-bases$/);
    // The body must be FormData (or a multipart-compatible value).
    expect(init.body).toBeInstanceOf(FormData);
    // The Content-Type must NOT be a hard-coded JSON header. Either
    // the header is absent (browser sets the boundary) or it is
    // `multipart/form-data` without a boundary (browser adds it).
    const headers = init.headers ?? {};
    const ct =
      (headers as Record<string, string>)["Content-Type"] ??
      (headers as Record<string, string>)["content-type"];
    if (ct !== undefined) {
      expect(ct.toLowerCase()).not.toBe("application/json");
    }
  });

  it("sets Content-Type to application/json for JSON bodies", async () => {
    const { createPlan } = await import("./api");
    try {
      await createPlan({ message: "hi" });
    } catch {
      // Same as above — the body shape is asserted via fetch.
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    const headers = (init.headers ?? {}) as Record<string, string>;
    const ct = headers["Content-Type"] ?? headers["content-type"];
    expect(ct?.toLowerCase()).toBe("application/json");
    expect(typeof init.body).toBe("string");
    expect(JSON.parse(init.body as string)).toEqual({ message: "hi" });
  });

  it("does not set Content-Type for upload (multipart body)", async () => {
    const { uploadKnowledgeDocument } = await import("./api");
    const file = new File(["hi"], "doc.txt", { type: "text/plain" });
    try {
      await uploadKnowledgeDocument("ai_introduction", file);
    } catch {
      // ignore
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    const headers = (init.headers ?? {}) as Record<string, string>;
    const ct = headers["Content-Type"] ?? headers["content-type"];
    if (ct !== undefined) {
      expect(ct.toLowerCase()).not.toBe("application/json");
      expect(ct.toLowerCase()).toMatch(/^multipart\/form-data/);
    }
    expect(init.body).toBeInstanceOf(FormData);
  });
});

describe("ApiError surface", () => {
  it("carries detail, code, and request_id from the backend", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: {
            code: "EMPTY_DOCUMENT",
            message: "no text could be extracted",
            request_id: "req-abc",
          },
        }),
        { status: 422, headers: { "content-type": "application/json" } },
      ),
    );
    const { listKnowledgeBases, ApiError } = await import("./api");
    let captured: unknown = null;
    try {
      await listKnowledgeBases();
    } catch (e) {
      captured = e;
    }
    expect(captured).toBeInstanceOf(ApiError);
    const err = captured as InstanceType<typeof ApiError>;
    expect(err.status).toBe(422);
    expect((err.body as { detail?: { code?: string } }).detail?.code).toBe(
      "EMPTY_DOCUMENT",
    );
    expect((err.body as { detail?: { request_id?: string } }).detail?.request_id).toBe(
      "req-abc",
    );
  });
});
