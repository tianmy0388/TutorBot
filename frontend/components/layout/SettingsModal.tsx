"use client";

/**
 * SettingsModal — global settings dialog.
 *
 * Currently exposes:
 *  - Theme switcher (dark / light). Switching here writes
 *    ``document.documentElement.dataset.theme`` and persists to
 *    localStorage so the choice survives reloads. The change is global
 *    because Tailwind colors resolve through CSS variables declared on
 *    :root / [data-theme=...] (see globals.css).
 */

import { useEffect } from "react";
import { X, Moon, Sun, Monitor } from "lucide-react";
import { useTutorStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function SettingsModal() {
  const open = useTutorStore((s) => s.settingsOpen);
  const setOpen = useTutorStore((s) => s.setSettingsOpen);
  const theme = useTutorStore((s) => s.theme);
  const setTheme = useTutorStore((s) => s.setTheme);

  // Close on Esc
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
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-fade-in"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-md rounded-xl border bg-bg-panel border-fg/10 shadow-2xl animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-fg/10">
          <h2 className="text-base font-semibold">设置</h2>
          <button
            onClick={() => setOpen(false)}
            className="p-1.5 rounded-lg text-fg-muted hover:text-fg hover:bg-bg-card transition-colors"
            title="关闭 (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 space-y-5">
          {/* Theme */}
          <section>
            <div className="text-[11px] uppercase tracking-wider text-fg-muted font-semibold mb-2">
              主题
            </div>
            <div className="grid grid-cols-2 gap-2">
              <ThemeOption
                label="深色"
                description="默认 — 适合长时间阅读"
                icon={Moon}
                selected={theme === "dark"}
                onClick={() => setTheme("dark")}
              />
              <ThemeOption
                label="浅色"
                description="明亮背景 — 适合打印/分享"
                icon={Sun}
                selected={theme === "light"}
                onClick={() => setTheme("light")}
              />
            </div>
            <p className="mt-2 text-[11px] text-fg-subtle">
              切换会立即生效并保存到浏览器;整个页面的颜色、按钮、卡片、滚动条都会跟着变化。
            </p>
          </section>

          {/* Future sections can be added here */}
          <section className="pt-3 border-t border-fg/10">
            <div className="text-[11px] text-fg-subtle">
              后续会加入:语言切换、模型选择、消息历史保留策略等。
            </div>
          </section>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-fg/10">
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
  description,
  icon: Icon,
  selected,
  onClick,
}: {
  label: string;
  description: string;
  icon: any;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "p-3 rounded-lg border text-left transition-all flex items-start gap-2",
        selected
          ? "border-brand-500 bg-brand-500/10 text-brand-200"
          : "border-fg/10 hover:border-fg/20 hover:bg-bg-card text-fg-muted hover:text-fg",
      )}
    >
      <Icon
        className={cn(
          "w-4 h-4 mt-0.5 shrink-0",
          selected ? "text-brand-300" : "text-fg-muted",
        )}
      />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-[11px] text-fg-subtle">{description}</div>
      </div>
    </button>
  );
}