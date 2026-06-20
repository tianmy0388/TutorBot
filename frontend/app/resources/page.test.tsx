/**
 * Tests for the /resources page.
 *
 * Regression: the page used to crash with
 *   "Cannot read properties of undefined (reading 'map')"
 * when the backend payload omitted the `types` field. The page must
 * render safely for any combination of missing/empty types.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

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
});
