"use client";

import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookMarked, BookOpenText, Files, Home, Moon, Settings, Sun } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  match: (path: string) => boolean;
}

const PRIMARY_NAV: NavItem[] = [
  { href: "/", label: "首页", icon: Home, match: (path) => path === "/" },
  { href: "/workspace", label: "学习", icon: BookOpenText, match: (path) => path.startsWith("/workspace") },
  { href: "/knowledge-bases", label: "资料库", icon: BookMarked, match: (path) => path.startsWith("/knowledge-bases") },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const theme = useTutorStore((state) => state.theme);
  const setTheme = useTutorStore((state) => state.setTheme);

  return (
    <div className="flex h-full bg-bg text-fg">
      <aside className="hidden w-[240px] shrink-0 flex-col border-r border-border bg-bg-panel px-4 py-5 md:flex">
        <Link href="/" className="flex min-h-11 items-center px-2" data-testid="app-logo">
          <Logo size={30} showWordmark wordmarkSize="lg" />
        </Link>

        <nav className="mt-10 space-y-1" aria-label="主要导航" data-testid="app-nav">
          {PRIMARY_NAV.map((item) => <DesktopNavItem key={item.href} item={item} pathname={pathname} />)}
        </nav>

        <div className="mt-8 px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-subtle">你的空间</div>
        <Link
          href="/resources"
          className={cn(
            "mt-2 flex min-h-11 items-center gap-3 rounded-2xl px-3 text-sm font-medium transition-colors",
            pathname.startsWith("/resources") ? "bg-bg-subtle text-fg" : "text-fg-muted hover:bg-bg-subtle hover:text-fg",
          )}
        >
          <Files className="h-[18px] w-[18px]" />
          最近生成
        </Link>

        <div className="mt-auto space-y-1 border-t border-border pt-4">
          <button
            type="button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="flex min-h-11 w-full items-center gap-3 rounded-2xl px-3 text-sm font-medium text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg"
            aria-label={theme === "dark" ? "切换到浅色模式" : "切换到暗色模式"}
            data-testid="nav-theme-toggle"
          >
            {theme === "dark" ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
            {theme === "dark" ? "浅色模式" : "暗色模式"}
          </button>
          <Link href="/settings" className="flex min-h-11 items-center gap-3 rounded-2xl px-3 text-sm font-medium text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg">
            <Settings className="h-[18px] w-[18px]" />
            设置
          </Link>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-hidden pb-[72px] md:pb-0">{children}</main>

      <nav className="fixed inset-x-0 bottom-0 z-50 grid h-[72px] grid-cols-3 border-t border-border bg-bg-panel/95 px-3 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden" aria-label="主要导航" data-testid="app-nav-mobile">
        {PRIMARY_NAV.map((item) => {
          const active = item.match(pathname);
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href} className={cn("flex min-h-11 flex-col items-center justify-center gap-1 rounded-2xl text-[11px] font-semibold transition-colors", active ? "text-fg" : "text-fg-muted")}>
              <Icon className={cn("h-5 w-5", active && "stroke-[2.5]")} />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

function DesktopNavItem({ item, pathname }: { item: NavItem; pathname: string }) {
  const active = item.match(pathname);
  const Icon = item.icon;
  return (
    <Link href={item.href} className={cn("flex min-h-11 items-center gap-3 rounded-2xl px-3 text-sm font-semibold transition-colors duration-200", active ? "bg-bg-subtle text-fg" : "text-fg-muted hover:bg-bg-subtle hover:text-fg")} data-testid={`nav-${item.label}`}>
      <Icon className={cn("h-[18px] w-[18px]", active && "stroke-[2.5]")} />
      {item.label}
    </Link>
  );
}
