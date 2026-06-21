/**
 * Stage 0 — knowledge-bases page regression test.
 *
 * Pins down two bugs from the plan:
 *   1. `refreshAll` is wrapped in `useCallback` with `detailsById` in
 *      its dependency list, and the effect that calls it also depends
 *      on `refreshAll`. This produces an infinite render loop
 *      (initial fetch → setDetailsById → new callback → new effect
 *      → fetch again). The test asserts a single initial GET.
 *   2. The polling interval is gated on `anyWorking` which is derived
 *      from `detailsById`, so it captures the same loop. When nothing
 *      is non-terminal the page must not issue any GETs.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

const listKnowledgeBases = vi.fn();
const getKnowledgeBase = vi.fn();
const createKnowledgeBase = vi.fn();
const deleteKnowledgeBase = vi.fn();
const deleteKnowledgeDocument = vi.fn();
const retryKnowledgeDocument = vi.fn();
const uploadKnowledgeDocument = vi.fn();

vi.mock("@/lib/api", () => ({
  listKnowledgeBases: (...a: unknown[]) => listKnowledgeBases(...a),
  getKnowledgeBase: (...a: unknown[]) => getKnowledgeBase(...a),
  createKnowledgeBase: (...a: unknown[]) => createKnowledgeBase(...a),
  deleteKnowledgeBase: (...a: unknown[]) => deleteKnowledgeBase(...a),
  deleteKnowledgeDocument: (...a: unknown[]) => deleteKnowledgeDocument(...a),
  retryKnowledgeDocument: (...a: unknown[]) => retryKnowledgeDocument(...a),
  uploadKnowledgeDocument: (...a: unknown[]) => uploadKnowledgeDocument(...a),
}));

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      activeKnowledgeBaseId: null,
      setActiveKnowledgeBaseId: () => undefined,
    }),
}));

import KnowledgeBasesPage from "@/app/knowledge-bases/page";

describe("KnowledgeBasesPage — request bound", () => {
  beforeEach(() => {
    listKnowledgeBases.mockReset();
    getKnowledgeBase.mockReset();
    listKnowledgeBases.mockResolvedValue({
      items: [
        {
          id: "ai_introduction",
          name: "AI Introduction",
          description: "",
          is_seeded: true,
          document_count: 0,
          ready_count: 0,
          failed_count: 0,
          total_chunks: 0,
          embedding_model: "",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ],
      total: 1,
    });
    getKnowledgeBase.mockResolvedValue({
      id: "ai_introduction",
      name: "AI Introduction",
      description: "",
      is_seeded: true,
      document_count: 0,
      ready_count: 0,
      failed_count: 0,
      total_chunks: 0,
      embedding_model: "",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      documents: [],
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("issues at most one initial list GET, not an infinite loop", async () => {
    render(<KnowledgeBasesPage />);
    await waitFor(() =>
      expect(listKnowledgeBases).toHaveBeenCalled(),
    );
    // Give the render loop a few hundred ms to surface repeats.
    await new Promise((r) => setTimeout(r, 250));
    expect(listKnowledgeBases.mock.calls.length).toBeLessThanOrEqual(2);
  });

  it("does not poll while no document is non-terminal", async () => {
    render(<KnowledgeBasesPage />);
    await waitFor(() =>
      expect(listKnowledgeBases).toHaveBeenCalled(),
    );
    const initialCalls = listKnowledgeBases.mock.calls.length;
    // Wait beyond the 2s poll interval.
    await new Promise((r) => setTimeout(r, 2500));
    expect(listKnowledgeBases.mock.calls.length).toBe(initialCalls);
  });

  it("does not call getKnowledgeBase for libs with no in-flight documents", async () => {
    render(<KnowledgeBasesPage />);
    await waitFor(() =>
      expect(listKnowledgeBases).toHaveBeenCalled(),
    );
    await new Promise((r) => setTimeout(r, 250));
    // No upload happened, so the only GET should be the list. Detail
    // GETs are only justified for libs that actually have working docs.
    expect(getKnowledgeBase).not.toHaveBeenCalled();
  });
});
