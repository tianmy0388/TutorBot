import type { Metadata } from "next";
import { Fraunces, Inter_Tight, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/layout/AppShell";
import { SettingsModal } from "@/components/layout/SettingsModal";
import { ThemeHydrator } from "@/components/layout/ThemeHydrator";

/**
 * TutorBot — Editorial Library Modernism typography stack.
 *
 *   Fraunces:        display (variable serif with optical sizing, soft warmth)
 *   Inter Tight:     body    (refined humanist sans, condensed counters)
 *   JetBrains Mono:  mono    (technical marks — session ids, hashes)
 *
 * We expose them as CSS variables consumed by Tailwind's fontFamily
 * (see tailwind.config.ts) and by raw CSS in globals.css.
 */

const fraunces = Fraunces({
  subsets: ["latin"],
  display: "swap",
  weight: "variable",
  variable: "--font-display",
  axes: ["opsz", "SOFT"],
});

const interTight = Inter_Tight({
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600", "700"],
  variable: "--font-body",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "TutorBot — 多智能体个性化学习",
  description:
    "TutorBot — 个性化学习资源生成多智能体系统。基于知识图谱与学习画像，生成讲解、习题、可视化与评估。",
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
    <html
      lang="zh-CN"
      suppressHydrationWarning
      className={`${fraunces.variable} ${interTight.variable} ${jetbrainsMono.variable}`}
    >
      <body className="bg-bg text-fg antialiased h-dvh overflow-hidden">
        <ThemeHydrator />
        <AppShell>{children}</AppShell>
        <SettingsModal />
      </body>
    </html>
  );
}
