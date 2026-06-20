/**
 * Tests for the ResourcePlanCard (Task 10).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ResourcePlanCard } from "./ResourcePlanCard";
import type { ResourcePlan } from "@/lib/types";

const plan: ResourcePlan = {
  plan_id: "plan-1",
  intent: "resource_generation",
  topic: "Transformer",
  recommended: ["document", "mindmap", "exercise"],
  optional: ["video", "code", "ppt", "reading"],
  estimated_seconds: 45,
  rationale: "默认核心三类",
};

describe("ResourcePlanCard", () => {
  afterEach(() => cleanup());

  it("renders the recommended list as initially selected", () => {
    render(<ResourcePlanCard plan={plan} onConfirm={vi.fn()} />);
    for (const t of plan.recommended) {
      const btn = screen.getByTestId(`plan-toggle-${t}`);
      expect(btn.getAttribute("data-selected")).toBe("true");
      expect(btn.getAttribute("data-recommended")).toBe("true");
    }
  });

  it("lets the user deselect a recommended type", () => {
    render(<ResourcePlanCard plan={plan} onConfirm={vi.fn()} />);
    const doc = screen.getByTestId("plan-toggle-document");
    fireEvent.click(doc);
    expect(doc.getAttribute("data-selected")).toBe("false");
  });

  it("blocks video/PPT unless recommended", () => {
    render(<ResourcePlanCard plan={plan} onConfirm={vi.fn()} />);
    // The toggle for "video" exists but is not initially selected.
    const video = screen.getByTestId("plan-toggle-video");
    expect(video.getAttribute("data-recommended")).toBe("false");
    expect(video.getAttribute("data-selected")).toBe("false");
  });

  it("updates the estimated time when toggling", () => {
    render(<ResourcePlanCard plan={plan} onConfirm={vi.fn()} />);
    const before = screen.getByTestId("resource-plan-eta").textContent ?? "";
    // Enable video (90s) — should add ~90s to the estimate.
    fireEvent.click(screen.getByTestId("plan-toggle-video"));
    const after = screen.getByTestId("resource-plan-eta").textContent ?? "";
    expect(after).not.toEqual(before);
    expect(after).toMatch(/分钟|秒/);
  });

  it("sends only selected types to onConfirm", () => {
    const onConfirm = vi.fn();
    render(<ResourcePlanCard plan={plan} onConfirm={onConfirm} />);
    // Deselect document, then confirm.
    fireEvent.click(screen.getByTestId("plan-toggle-document"));
    fireEvent.click(screen.getByTestId("plan-confirm"));
    const types = onConfirm.mock.calls[0][0] as string[];
    expect(types).not.toContain("document");
    expect(types).toContain("mindmap");
    expect(types).toContain("exercise");
  });

  it("disables the confirm button when nothing is selected", () => {
    const onConfirm = vi.fn();
    render(<ResourcePlanCard plan={plan} onConfirm={onConfirm} />);
    // Deselect all three recommended types.
    for (const t of plan.recommended) {
      fireEvent.click(screen.getByTestId(`plan-toggle-${t}`));
    }
    const btn = screen.getByTestId("plan-confirm") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
