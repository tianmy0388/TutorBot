import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WebSearchToggle } from "./WebSearchToggle";

afterEach(cleanup);


describe("WebSearchToggle", () => {
  it("is an accessible default-off keyboard-operable switch", () => {
    const onChange = vi.fn();
    render(<WebSearchToggle checked={false} onChange={onChange} />);

    const toggle = screen.getByRole("switch", { name: "联网查资料" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.keyDown(toggle, { key: "Enter" });
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("is disabled while persistence is pending and shows a non-blocking error", () => {
    render(
      <WebSearchToggle
        checked
        disabled
        error="设置保存失败，已恢复先前状态"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("switch", { name: "联网查资料" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("设置保存失败");
  });
});
