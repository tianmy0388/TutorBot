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
        aria-label="联网查资料"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "flex min-h-11 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors",
          checked
            ? "border-border bg-bg-panel text-fg shadow-sm"
            : "border-border bg-transparent text-fg-muted hover:bg-bg-panel/70 hover:text-fg",
          disabled && "opacity-50 cursor-wait",
        )}
      >
        <Globe2 className="w-3 h-3" aria-hidden="true" />
        联网查资料
      </button>
      {error ? (
        <span role="status" className="text-[11px] text-fg-muted">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export default WebSearchToggle;
