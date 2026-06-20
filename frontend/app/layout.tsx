import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/layout/AppShell";
import { SettingsModal } from "@/components/layout/SettingsModal";
import { ThemeHydrator } from "@/components/layout/ThemeHydrator";

export const metadata: Metadata = {
  title: "Tutor — Multi-Agent Learning",
  description: "个性化学习资源生成多智能体系统",
};

/**
 * Theme is applied client-side via ``document.documentElement.dataset.theme``
 * from the Zustand store. We deliberately do NOT set a default ``class``
 * here — ``globals.css`` uses ``:root`` + ``[data-theme="dark"]`` so the
 * CSS variables have valid values even before hydration. The store's
 * ``hydrateTheme()`` runs on mount and sets ``data-theme`` to the user's
 * stored preference (or keeps the dark default).
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className="bg-bg text-fg antialiased min-h-screen">
        <ThemeHydrator />
        <AppShell>{children}</AppShell>
        <SettingsModal />
      </body>
    </html>
  );
}