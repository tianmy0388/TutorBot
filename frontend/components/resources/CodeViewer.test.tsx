import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

import type { Resource } from "@/lib/types";
import { CodeViewer } from "./CodeViewer";

vi.mock("@/lib/store", () => ({
  useTutorStore: (
    selector: (state: {
      userId: string;
      latestPackage: { package_id: string } | null;
    }) => unknown,
  ) => selector({ userId: "local-user", latestPackage: null }),
}));

function codeResource(): Resource {
  return {
    resource_id: "resource/code 1",
    type: "code",
    title: "Matplotlib demo",
    content: "print('ok')",
    format_specific: {
      language: "python",
      code: "print('ok')",
      artifacts: [
        { name: "figure 1.png", kind: "png", path: "C:\\private\\figure 1.png" },
        { name: "diagram.svg", kind: "svg", path: "artifacts/private.svg" },
        { name: "report.pdf", kind: "pdf", path: "C:\\private\\report.pdf" },
      ],
    },
    difficulty: 2,
    estimated_minutes: 5,
    prerequisites: [],
    generated_by: ["code-agent"],
    confidence_score: 0.9,
    topic: "matplotlib",
    tags: [],
    created_at: "2026-07-18T00:00:00Z",
    metadata: { package_id: "package/code 1" },
  };
}

afterEach(() => cleanup());

describe("CodeViewer image artifacts", () => {
  it("renders canonical owned URLs as natural semantic preview buttons", () => {
    render(<CodeViewer resource={codeResource()} />);
    const first = screen.getByRole("button", { name: "查看 figure 1.png" });
    const second = screen.getByRole("button", { name: "查看 diagram.svg" });
    const firstImage = screen.getByRole("img", { name: "figure 1.png" });

    expect(first).not.toHaveAttribute("target");
    expect(second).not.toHaveAttribute("target");
    expect(firstImage).toHaveAttribute(
      "src",
      "/api/v1/resources/packages/local-user/package%2Fcode%201/resources/resource%2Fcode%201/artifacts/figure%201.png",
    );
    expect(firstImage.getAttribute("src")).not.toContain("private");
    expect(firstImage).toHaveClass(
      "image-artifact-preview",
      "object-contain",
      "h-auto",
    );
  });

  it("opens the selected image in-page and navigates across image filenames", async () => {
    render(<CodeViewer resource={codeResource()} />);
    const opener = screen.getByRole("button", { name: "查看 figure 1.png" });
    opener.focus();
    fireEvent.click(opener);

    const dialog = screen.getByRole("dialog", { name: "图片查看器" });
    expect(dialog).toBeVisible();
    expect(within(dialog).getByText("figure 1.png")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一张图片" }));
    expect(within(dialog).getByRole("img", { name: "diagram.svg" })).toHaveAttribute(
      "src",
      "/api/v1/resources/packages/local-user/package%2Fcode%201/resources/resource%2Fcode%201/artifacts/diagram.svg",
    );

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("preserves PDF download links on the same canonical artifact endpoint", () => {
    render(<CodeViewer resource={codeResource()} />);
    const pdf = screen.getByRole("link", { name: /report\.pdf/ });
    expect(pdf).toHaveAttribute(
      "href",
      "/api/v1/resources/packages/local-user/package%2Fcode%201/resources/resource%2Fcode%201/artifacts/report.pdf",
    );
    expect(pdf.getAttribute("href")).not.toContain("private");
  });

  it("detects image extensions without kind and keeps the id-only URL fallback", () => {
    const resource = codeResource();
    resource.metadata = { package_id: "pending-job" };
    resource.format_specific = {
      ...resource.format_specific,
      artifacts: [
        { name: "fallback.JPEG", path: "D:\\must-not-leak\\fallback.JPEG" },
      ],
    };

    render(<CodeViewer resource={resource} />);
    expect(
      screen.getByRole("button", { name: "查看 fallback.JPEG" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "fallback.JPEG" })).toHaveAttribute(
      "src",
      "/api/v1/resources/local-user/resources/resource%2Fcode%201/artifacts/fallback.JPEG",
    );
  });
});
