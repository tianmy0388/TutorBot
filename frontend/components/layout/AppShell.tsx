"use client";

/**
 * AppShell — TutorBot's outer chrome.
 *
 * Layout contract:
 *   body  →  ``h-dvh overflow-hidden`` (set in app/layout.tsx)
 *   AppShell → ``h-full`` (fills the body, no min-height tricks)
 *   AppShell → column: top rail + scroll region
 *
 * The top rail is a single horizontal band with three "zones":
 *   left:  brand wordmark + lockup
 *   middle: primary nav (the four top-level surfaces)
 *   right: status + theme + settings
 *
 * We deliberately avoid a sticky shadow; the rail sits on a slightly
 * elevated panel color with a 1px hairline divider — more editorial,
 * less SaaS-app.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  Database,
  MessageSquare,
  Settings as SettingsIcon,
  Sun,
  Moon,
  Sparkle,
} from "lucide-react";
import { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useTutorStore } from "@/lib/store";
import { Logo } from "@/components/brand/Logo";

interface NavItem {
  href: string;
  label: string;
  icon: any;
  match: (path: string) => boolean;
}

const NAV_ITEMS: NavItem[] = [
  {
    href: "/",
    label: "学习工作台",
    icon: MessageSquare,
    match: (p) => p === "/",
  },
  {
    href: "/knowledge-bases",
    label: "知识库",
    icon: Database,
    match: (p) => p.startsWith("/knowledge-bases"),
  },
  {
    href: "/resources",
    label: "资源中心",
    icon: BookOpen,
    match: (p) => p.startsWith("/resources"),
  },
  {
    href: "/settings",
    label: "设置",
    icon: SettingsIcon,
    match: (p) => p.startsWith("/settings"),
  },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const setSettingsOpen = useTutorStore((s) => s.setSettingsOpen);
  const theme = useTutorStore((s) => s.theme);
  const setTheme = useTutorStore((s) => s.setTheme);

  return (
    <div className="h-full flex flex-col bg-bg text-fg">
      <header
        className="border-b shrink-0 z-30 backdrop-blur-sm animate-slide-down"
        style={{
          backgroundColor: "rgb(var(--color-bg-panel) / 0.85)",
          borderColor: "rgb(var(--color-rule) / 0.6)",
        }}
      >
        <div className="h-14 px-2 sm:px-5 flex items-center justify-between gap-1 sm:gap-6">
          {/* Brand */}
          <Link
            href="/"
            className="group hidden sm:flex items-center"
            data-testid="app-logo"
          >
            <Logo size={26} showWordmark wordmarkSize="lg" />
          </Link>

          {/* Primary nav */}
          <nav
            className="flex items-center gap-0 sm:gap-1 flex-1 justify-start sm:justify-center"
            data-testid="app-nav"
          >
            {NAV_ITEMS.map((item) => {
              const active = item.match(pathname);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "relative inline-flex items-center gap-1.5 px-2 sm:px-3 h-8 rounded-md text-[13px] font-medium",
                    "transition-colors duration-150",
                    active
                      ? "text-fg"
                      : "text-fg-muted hover:text-fg",
                  )}
                  data-testid={`nav-${item.label}`}
                  aria-label={item.label}
                >
                  <item.icon
                    className={cn(
                      "w-3.5 h-3.5 transition-colors",
                      active ? "text-brand-400" : "text-fg-subtle",
                    )}
                  />
                  <span className="hidden min-[480px]:inline">{item.label}</span>
                  {active && (
                    <span
                      className="absolute -bottom-[1px] left-3 right-3 h-[2px] rounded-full"
                      style={{ backgroundColor: "rgb(var(--color-brand-400))" }}
                    />
                  )}
                </Link>
              );
            })}
          </nav>

          {/* Right cluster */}
          <div className="flex items-center gap-1.5">
            <span className="hidden md:inline-flex items-center gap-1.5 px-2.5 h-7 rounded-full text-[10px] font-mono-tab text-fg-subtle"
              style={{
                backgroundColor: "rgb(var(--color-bg-card) / 0.5)",
                border: "1px solid rgb(var(--color-rule) / 0.5)",
                letterSpacing: "0.14em",
              }}
            >
              <span
                className="w-1.5 h-1.5 rounded-full animate-pulse-slow"
                style={{ backgroundColor: "rgb(var(--color-brand-400))" }}
              />
              v1.0
            </span>

            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="p-1.5 rounded-md text-fg-muted hover:text-fg transition-colors hover:bg-bg-card"
              title={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}
              data-testid="nav-theme-toggle"
            >
              {theme === "dark" ? (
                <Sun className="w-4 h-4" />
              ) : (
                <Moon className="w-4 h-4" />
              )}
            </button>

            <button
              className="btn-secondary text-xs h-8"
              onClick={() => setSettingsOpen(true)}
              title="打开设置"
            >
              <Sparkle className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">设置</span>
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1 min-h-0 overflow-hidden">{children}</main>
    </div>
  );
}
