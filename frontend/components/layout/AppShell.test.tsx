import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AppShell } from "./AppShell";

let pathname = "/";
const setTheme = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

vi.mock("@/lib/store", () => ({
  useTutorStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ theme: "light", setTheme }),
}));

describe("AppShell", () => {
  beforeEach(() => {
    pathname = "/";
    setTheme.mockReset();
  });

  afterEach(() => cleanup());

  it("keeps the primary navigation focused on the student workflow", () => {
    render(<AppShell><div>页面内容</div></AppShell>);

    const navigation = within(screen.getByTestId("app-nav"));
    expect(navigation.getByRole("link", { name: "首页" })).toBeTruthy();
    expect(navigation.getByRole("link", { name: "学习" })).toBeTruthy();
    expect(navigation.getByRole("link", { name: "资料库" })).toBeTruthy();
    expect(navigation.queryByText("比赛演示")).toBeNull();
    expect(navigation.queryByText("设置")).toBeNull();
  });

  it("marks the current destination and exposes the same mobile destinations", () => {
    pathname = "/workspace";
    render(<AppShell><div>学习页面</div></AppShell>);

    expect(within(screen.getByTestId("app-nav")).getByRole("link", { name: "学习" }).className).toContain("bg-bg-subtle");
    const mobileNavigation = within(screen.getByTestId("app-nav-mobile"));
    expect(mobileNavigation.getByRole("link", { name: "首页" })).toBeTruthy();
    expect(mobileNavigation.getByRole("link", { name: "学习" })).toBeTruthy();
    expect(mobileNavigation.getByRole("link", { name: "资料库" })).toBeTruthy();
  });

  it("switches to dark mode through the sidebar control", () => {
    render(<AppShell><div>页面内容</div></AppShell>);
    fireEvent.click(screen.getByTestId("nav-theme-toggle"));
    expect(setTheme).toHaveBeenCalledWith("dark");
  });
});
