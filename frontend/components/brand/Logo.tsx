"use client";

/**
 * TutorBot brand lockup. The mark lives as a reusable brand asset rather
 * than an inline decorative illustration.
 */

import { cn } from "@/lib/utils";

export interface LogoProps {
  /** Render size in pixels (square). */
  size?: number;
  /** Optional className for the outer wrapper. */
  className?: string;
  /** Show the wordmark to the right of the mark. */
  showWordmark?: boolean;
  /** Wordmark size tier — "lg" for headers, "sm" for rails. */
  wordmarkSize?: "lg" | "sm";
}

export function Logo({
  size = 32,
  className,
  showWordmark = false,
  wordmarkSize = "lg",
}: LogoProps) {
  return (
    <div className={cn("inline-flex items-center gap-2.5", className)}>
      <img
        src="/brand/tutorbot-mark.svg"
        alt=""
        className="brand-mark"
        width={size}
        height={size}
        style={{ flexShrink: 0 }}
      />

      {showWordmark && (
        <div className="leading-none flex items-baseline gap-1.5">
          <span
            className={cn(
              "font-semibold tracking-[0.02em]",
              wordmarkSize === "lg" ? "text-[17px]" : "text-sm",
            )}
          >
            TutorBot
          </span>
          {wordmarkSize === "lg" && (
            <span className="text-[10px] font-medium text-fg-subtle">学习空间</span>
          )}
        </div>
      )}
    </div>
  );
}
