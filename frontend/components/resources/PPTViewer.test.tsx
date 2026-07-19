import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { Resource } from "@/lib/types";
import { PPTViewer } from "./PPTViewer";

vi.mock("@/lib/store", () => ({
  useTutorStore: (
    selector: (state: { userId: string; latestPackage: null }) => unknown,
  ) => selector({ userId: "local-user", latestPackage: null }),
}));

const fetchMock = vi.fn();

function pptResource(formatSpecific: Record<string, unknown>): Resource {
  return {
    resource_id: "ppt-resource",
    type: "ppt",
    title: "Portable deck",
    content: "",
    format_specific: formatSpecific,
    difficulty: 2,
    estimated_minutes: 5,
    prerequisites: [],
    generated_by: [],
    confidence_score: 0.8,
    topic: "PPT",
    tags: [],
    created_at: "2026-07-17T00:00:00Z",
    metadata: { package_id: "package-ppt" },
  };
}

beforeEach(() => {
  fetchMock.mockResolvedValue(new Response(new Blob(["pptx"]), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:ppt"),
    revokeObjectURL: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PPTViewer download", () => {
  it.each([
    ["canonical artifact key", { artifact_key: "ppt/package-ppt/deck.pptx" }],
    ["legacy path", { pptx_path: "C:\\legacy\\deck.pptx" }],
  ])("uses the package endpoint for %s", async (_shape, formatSpecific) => {
    render(<PPTViewer resource={pptResource(formatSpecific)} />);

    const button = screen.getByRole("button", { name: "下载 .pptx" });
    expect(button).toBeEnabled();
    fireEvent.click(button);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/resources/packages/local-user/package-ppt/resources/ppt-resource/download",
      ),
    );
  });
});
