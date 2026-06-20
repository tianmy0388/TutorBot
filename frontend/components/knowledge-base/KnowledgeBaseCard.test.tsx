/**
 * Tests for the KnowledgeBaseCard component (Task 9).
 *
 * Pins the key UI invariants: counts, status pills, retry on failure,
 * select-as-active update.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { KnowledgeBaseCard } from "./KnowledgeBaseCard";
import type { KnowledgeBaseDetail } from "@/lib/types";

const baseDetail: KnowledgeBaseDetail = {
  id: "kb-1",
  name: "测试库",
  description: "用于单元测试",
  is_seeded: false,
  document_count: 2,
  ready_count: 1,
  failed_count: 1,
  total_chunks: 12,
  embedding_model: "text-embedding-3-small",
  created_at: "2026-06-20T00:00:00Z",
  updated_at: "2026-06-20T00:00:00Z",
  documents: [
    {
      id: "doc-1",
      knowledge_base_id: "kb-1",
      display_name: "ready.pdf",
      source_filename: "ready.pdf",
      extension: ".pdf",
      size_bytes: 1234,
      checksum: "x",
      status: "ready",
      chunk_count: 9,
      embedding_model: "text-embedding-3-small",
      error: null,
      error_code: null,
      created_at: "2026-06-20T00:00:00Z",
      updated_at: "2026-06-20T00:00:00Z",
    },
    {
      id: "doc-2",
      knowledge_base_id: "kb-1",
      display_name: "broken.pdf",
      source_filename: "broken.pdf",
      extension: ".pdf",
      size_bytes: 99,
      checksum: "y",
      status: "failed",
      chunk_count: 0,
      embedding_model: "",
      error: "PDF 无可提取文本",
      error_code: "EMPTY_DOCUMENT",
      created_at: "2026-06-20T00:00:00Z",
      updated_at: "2026-06-20T00:00:00Z",
    },
  ],
};

describe("KnowledgeBaseCard", () => {
  afterEach(() => cleanup());

  it("displays document/chunk counts and the failed badge", () => {
    render(
      <KnowledgeBaseCard
        detail={baseDetail}
        isActive={false}
        onSelect={() => undefined}
        onUpload={async () => undefined}
        onRetry={async () => undefined}
        onDelete={async () => undefined}
        onDeleteLibrary={async () => undefined}
      />,
    );
    expect(screen.getByText("2 份文档")).toBeTruthy();
    expect(screen.getByText("12 块")).toBeTruthy();
    expect(screen.getByText("1 就绪")).toBeTruthy();
    expect(screen.getByText("1 失败")).toBeTruthy();
  });

  it("marks the card as 当前 when active", () => {
    render(
      <KnowledgeBaseCard
        detail={baseDetail}
        isActive
        onSelect={() => undefined}
        onUpload={async () => undefined}
        onRetry={async () => undefined}
        onDelete={async () => undefined}
        onDeleteLibrary={async () => undefined}
      />,
    );
    expect(screen.getByText("当前")).toBeTruthy();
  });

  it("renders a retry button for failed documents", () => {
    render(
      <KnowledgeBaseCard
        detail={baseDetail}
        isActive
        onSelect={() => undefined}
        onUpload={async () => undefined}
        onRetry={async () => undefined}
        onDelete={async () => undefined}
        onDeleteLibrary={async () => undefined}
      />,
    );
    expect(screen.getByTestId("kb-doc-doc-2-retry")).toBeTruthy();
  });

  it("calls onRetry when the user clicks the retry button", async () => {
    const onRetry = vi.fn().mockResolvedValue(undefined);
    render(
      <KnowledgeBaseCard
        detail={baseDetail}
        isActive
        onSelect={() => undefined}
        onUpload={async () => undefined}
        onRetry={onRetry}
        onDelete={async () => undefined}
        onDeleteLibrary={async () => undefined}
      />,
    );
    fireEvent.click(screen.getByTestId("kb-doc-doc-2-retry"));
    expect(onRetry).toHaveBeenCalledWith("doc-2");
  });

  it("calls onSelect when the user picks a non-active library", () => {
    const onSelect = vi.fn();
    render(
      <KnowledgeBaseCard
        detail={baseDetail}
        isActive={false}
        onSelect={onSelect}
        onUpload={async () => undefined}
        onRetry={async () => undefined}
        onDelete={async () => undefined}
        onDeleteLibrary={async () => undefined}
      />,
    );
    // The card prefixes every testid with `kb-${detail.id}` and
    // baseDetail.id is "kb-1", so the select button is at "kb-kb-1-select".
    fireEvent.click(screen.getByTestId("kb-kb-1-select"));
    expect(onSelect).toHaveBeenCalled();
  });
});
