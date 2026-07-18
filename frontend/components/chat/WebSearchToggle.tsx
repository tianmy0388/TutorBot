"use client";

import { Globe2 } from "lucide-react";

import { cn } from "@/lib/utils";

export interface WebSearchToggleProps {
  checked: boolean;
  disabled?: boolean;
  error?: string | null;
  onChange: (enabled: boolean) => void;
}

export function WebSearchToggle({
  checked,
  disabled = false,
  error = null,
  onChange,
}: WebSearchToggleProps) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <button
        type="button"
        role="switch"
        aria-label="联网搜索"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "px-2.5 py-1 rounded-full text-xs transition-colors flex items-center gap-1.5",
          checked
            ? "bg-sky-500/20 text-sky-300 border border-sky-400/30"
            : "bg-bg-card text-fg-muted border border-fg/10",
          disabled && "opacity-50 cursor-wait",
        )}
      >
        <Globe2 className="w-3 h-3" aria-hidden="true" />
        联网搜索
      </button>
      {error ? (
        <span role="status" className="text-[11px] text-amber-300">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export default WebSearchToggle;
