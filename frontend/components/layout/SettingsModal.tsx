"use client";

/**
 * SettingsModal — TutorBot settings dialog (2026-07 redesign).
 *
 * Visual: editorial modal with a clean hairline frame, monospaced
 * metadata labels, and a clear primary action — no shadow gimmicks.
 */

import { useEffect } from "react";
import { X, Moon, Sun } from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import { Logo } from "@/components/brand/Logo";

export function SettingsModal() {
  const open = useTutorStore((s) => s.settingsOpen);
  const setOpen = useTutorStore((s) => s.setSettingsOpen);
  const theme = useTutorStore((s) => s.theme);
  const setTheme = useTutorStore((s) => s.setTheme);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in"
      style={{ backgroundColor: "rgb(0 0 0 / 0.55)", backdropFilter: "blur(8px)" }}
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-md rounded-xl overflow-hidden animate-scale-in"
        style={{
          backgroundColor: "rgb(var(--color-bg-panel))",
          border: "1px solid rgb(var(--color-rule))",
          boxShadow: "0 30px 60px -20px rgb(0 0 0 / 0.5)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 h-14"
          style={{ borderBottom: "1px solid rgb(var(--color-rule) / 0.6)" }}
        >
          <div className="flex items-center gap-2.5">
            <Logo size={22} />
            <div className="leading-tight">
              <div className="font-display font-semibold text-sm">设置</div>
              <div className="text-[10px] font-mono-tab text-fg-subtle"
                style={{ letterSpacing: "0.12em" }}
              >
                PREFERENCES
              </div>
            </div>
          </div>
          <button
            onClick={() => setOpen(false)}
            className="p-1.5 rounded-md text-fg-muted hover:text-fg hover:bg-bg-card transition-colors"
            title="关闭 (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 space-y-5">
          <section>
            <div className="rail-label mb-3">主题 · Theme</div>
            <div className="grid grid-cols-2 gap-2">
              <ThemeOption
                label="深色"
                hint="Dark"
                description="默认 — 适合长时间阅读"
                icon={Moon}
                selected={theme === "dark"}
                onClick={() => setTheme("dark")}
              />
              <ThemeOption
                label="浅色"
                hint="Light"
                description="明亮背景 — 适合打印/分享"
                icon={Sun}
                selected={theme === "light"}
                onClick={() => setTheme("light")}
              />
            </div>
            <p className="mt-3 text-[11px] text-fg-subtle leading-relaxed">
              切换会立即生效并保存到浏览器。整个页面的颜色、按钮、卡片、滚动条都会跟着变化。
            </p>
          </section>

          <section
            className="pt-3"
            style={{ borderTop: "1px solid rgb(var(--color-rule) / 0.5)" }}
          >
            <div className="text-[11px] text-fg-subtle leading-relaxed">
              后续会加入：语言切换、消息历史保留策略等。AI 服务、密钥与连接测试已迁移到&nbsp;
              <a
                className="underline underline-offset-2"
                style={{ color: "var(--color-brand-300)" }}
                href="/settings"
              >
                /settings
              </a>
              &nbsp;页面。
            </div>
          </section>
        </div>

        {/* Footer */}
        <div
          className="flex items-center justify-end px-5 h-12"
          style={{
            borderTop: "1px solid rgb(var(--color-rule) / 0.6)",
            backgroundColor: "rgb(var(--color-bg) / 0.3)",
          }}
        >
          <button
            onClick={() => setOpen(false)}
            className="btn-primary text-sm h-9"
          >
            完成
          </button>
        </div>
      </div>
    </div>
  );
}

function ThemeOption({
  label,
  hint,
  description,
  icon: Icon,
  selected,
  onClick,
}: {
  label: string;
  hint: string;
  description: string;
  icon: any;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "p-3 rounded-md text-left transition-all flex items-start gap-3",
      )}
      style={{
        border: selected
          ? "1px solid rgb(var(--color-brand-400))"
          : "1px solid rgb(var(--color-rule))",
        backgroundColor: selected
          ? "rgb(var(--color-brand-400) / 0.08)"
          : "rgb(var(--color-bg-card) / 0.3)",
      }}
    >
      <Icon
        className="w-4 h-4 mt-0.5 shrink-0"
        style={{
          color: selected ? "var(--color-brand-400)" : "var(--color-fg-muted)",
        }}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{label}</span>
          <span className="text-[9px] font-mono-tab text-fg-subtle uppercase"
            style={{ letterSpacing: "0.14em" }}
          >
            {hint}
          </span>
        </div>
        <div className="text-[11px] text-fg-subtle mt-0.5">{description}</div>
      </div>
    </button>
  );
}
