/**
 * Tests for the /resources page.
 *
 * Regression coverage:
 *   1. The page used to crash with
 *      "Cannot read properties of undefined (reading 'map')"
 *      when the backend payload omitted the `types` field.
 *   2. The package preview used to be a flat list with no
 *      selection state, so the user couldn't actually look at a
 *      single resource — they only saw metadata. The preview
 *      now uses ResourceDetail and supports a selected state.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// Mock the API client so we don't need a real backend in unit tests.
const listResourcePackages = vi.fn();
const getResourcePackageDetail = vi.fn();

vi.mock("@/lib/api", () => ({
  listResourcePackages: (...args: unknown[]) => listResourcePackages(...args),
  getResourcePackageDetail: (...args: unknown[]) =>
    getResourcePackageDetail(...args),
}));

// Mock the Zustand store to provide a stable userId.
vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (s: { userId: string }) => string) =>
    selector({ userId: "u-test" }),
}));

// ResourceCard's per-type viewers pull in heavy deps; render a
// minimal ResourceDetail that exposes the resource's title so we
// can assert selection changes the rendered content.
vi.mock("@/components/resources/ResourceCard", () => ({
  ResourceDetail: ({ resource }: { resource: { resource_id: string; title: string; type: string } }) => (
    <div data-testid="resource-detail-mock">
      <span data-testid="resource-detail-title">{resource.title}</span>
      <span data-testid="resource-detail-type">{resource.type}</span>
    </div>
  ),
}));

import ResourcesPage from "@/app/resources/page";

describe("ResourcesPage", () => {
  afterEach(() => {
    cleanup();
    listResourcePackages.mockReset();
    getResourcePackageDetail.mockReset();
  });

  it("renders a package whose types array is missing without crashing", async () => {
    listResourcePackages.mockResolvedValueOnce({
      user_id: "u-test",
      total: 1,
      limit: 50,
      offset: 0,
      items: [
        {
          package_id: "pkg-1",
          topic: "Transformer",
          resource_count: 3,
          total_minutes: 30,
          // types intentionally undefined
          types: undefined as unknown as string[],
          avg_confidence: 0.8,
          created_at: new Date().toISOString(),
        },
      ],
    });

    render(<ResourcesPage />);
    await waitFor(() =>
      expect(screen.getByTestId("resource-card-pkg-1")).toBeInTheDocument(),
    );
    // Should not throw, and should fall back to "—" instead of crashing.
    expect(screen.getByText(/平均置信度 80%/)).toBeInTheDocument();
  });

  it("renders Chinese type labels when types is a string array", async () => {
    listResourcePackages.mockResolvedValueOnce({
      user_id: "u-test",
      total: 1,
      limit: 50,
      offset: 0,
      items: [
        {
          package_id: "pkg-2",
          topic: "CPU 调度",
          resource_count: 2,
          total_minutes: 20,
          types: ["document", "exercise"],
          avg_confidence: 0.65,
          created_at: new Date().toISOString(),
        },
      ],
    });

    render(<ResourcesPage />);
    await waitFor(() =>
      expect(screen.getByTestId("resource-card-pkg-2")).toBeInTheDocument(),
    );
    expect(screen.getByText(/文档.*练习/)).toBeInTheDocument();
    expect(screen.getByText(/平均置信度 65%/)).toBeInTheDocument();
  });

  it("opens a two-pane preview and switches between resources", async () => {
    listResourcePackages.mockResolvedValueOnce({
      user_id: "u-test",
      total: 1,
      limit: 50,
      offset: 0,
      items: [
        {
          package_id: "pkg-3",
          topic: "Transformer",
          resource_count: 2,
          total_minutes: 20,
          types: ["document", "mindmap"],
          avg_confidence: 0.7,
          created_at: new Date().toISOString(),
        },
      ],
    });
    getResourcePackageDetail.mockResolvedValueOnce({
      package_id: "pkg-3",
      topic: "Transformer",
      user_id: "u-test",
      resources: [
        {
          resource_id: "r-doc",
          type: "document",
          title: "文档: 注意力机制",
          content: "正文",
          difficulty: 2,
          estimated_minutes: 5,
          confidence_score: 0.7,
        },
        {
          resource_id: "r-map",
          type: "mindmap",
          title: "思维导图: Transformer",
          content: "",
          difficulty: 2,
          estimated_minutes: 3,
          confidence_score: 0.6,
        },
      ],
      created_at: new Date().toISOString(),
      metadata: {},
      target_profile_snapshot: {},
      learning_path_summary: {},
      generated_by: [],
    });

    render(<ResourcesPage />);
    const card = await screen.findByTestId("resource-card-pkg-3");
    fireEvent.click(card);

    await waitFor(() =>
      expect(screen.getByTestId("resource-package-preview")).toBeInTheDocument(),
    );
    // First resource is the default selection.
    expect(screen.getByTestId("resource-detail-title").textContent).toBe(
      "文档: 注意力机制",
    );
    // Switch to the second.
    fireEvent.click(screen.getByTestId("resource-list-item-r-map"));
    expect(screen.getByTestId("resource-detail-title").textContent).toBe(
      "思维导图: Transformer",
    );
    // The detail re-render must NOT re-fetch the package.
    expect(getResourcePackageDetail).toHaveBeenCalledTimes(1);
  });
});
