import type { Metadata } from "next";
import { JetBrains_Mono, Noto_Sans_SC, Noto_Serif_SC } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/layout/AppShell";
import { ThemeHydrator } from "@/components/layout/ThemeHydrator";

const themeBootScript = `try{const stored=localStorage.getItem("tutor:theme");document.documentElement.dataset.theme=stored==="dark"?"dark":"light"}catch{document.documentElement.dataset.theme="light"}`;

const notoSans = Noto_Sans_SC({
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600", "700"],
  variable: "--font-body",
});

const notoSerif = Noto_Serif_SC({
  subsets: ["latin"],
  display: "swap",
  weight: ["500", "600", "700"],
  variable: "--font-display",
});

const jetBrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "TutorBot · 你的学习空间",
  description: "从课程资料出发，继续学习、练习薄弱点，并整理下一步。",
  icons: { icon: "/brand/tutorbot-mark.svg" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="zh-CN"
      suppressHydrationWarning
      className={`${notoSans.variable} ${notoSerif.variable} ${jetBrainsMono.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body className="h-dvh overflow-hidden bg-bg text-fg antialiased">
        <ThemeHydrator />
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
