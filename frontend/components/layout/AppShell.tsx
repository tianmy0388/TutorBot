"use client";

/**
 * AppShell — the four-page navigation wrapper.
 *
 * - /                  learning workspace (chat + plan confirm + job tray)
 * - /knowledge-bases   library manager
 * - /resources         persisted resource center
 * - /settings          runtime configuration
 *
 * Capability buttons are no longer in the global header: the user's
 * intent is inferred by the router/plan flow inside the workspace.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  Database,
  MessageSquare,
  Settings as SettingsIcon,
} from "lucide-react";
import { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useTutorStore } from "@/lib/store";

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

  return (
    <div className="min-h-screen flex flex-col bg-bg text-fg">
      <header className="border-b border-fg/10 bg-bg-panel/80 backdrop-blur sticky top-0 z-30">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-12 flex items-center justify-between">
          <Link
            href="/"
            className="text-sm font-bold tracking-tight"
            data-testid="app-logo"
          >
            DeepTutor
          </Link>
          <nav className="flex items-center gap-1" data-testid="app-nav">
            {NAV_ITEMS.map((item) => {
              const active = item.match(pathname);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "inline-flex items-center gap-1.5 px-3 h-8 rounded-lg text-sm transition-colors",
                    active
                      ? "bg-brand-500/15 text-brand-200"
                      : "text-fg-muted hover:text-fg hover:bg-bg-card",
                  )}
                  data-testid={`nav-${item.label}`}
                >
                  <item.icon className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">{item.label}</span>
                </Link>
              );
            })}
          </nav>
          <button
            className="btn-secondary text-xs h-8"
            onClick={() => setSettingsOpen(true)}
            data-testid="nav-theme-toggle"
            title="主题"
          >
            主题
          </button>
        </div>
      </header>
      <main className="flex-1">{children}</main>
    </div>
  );
}
