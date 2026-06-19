import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tutor — Multi-Agent Learning",
  description: "个性化学习资源生成多智能体系统",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="bg-bg text-fg antialiased min-h-screen">{children}</body>
    </html>
  );
}
