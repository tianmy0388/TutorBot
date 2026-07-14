"use client";

/**
 * TutorBot Logo — distinctive monogram replacing the generic "T".
 *
 * Design intent: an open-book silhouette merged with a speech bubble's
 * gentle curve. The mark is composed of two facing pages whose inner
 * edges lift into a soft "smile" — quietly suggesting both reading and
 * conversation. A single saffron rule across the spine anchors the form
 * and ties the mark to the brand color.
 *
 * The SVG uses currentColor for strokes/fills so it inherits text color,
 * and a CSS variable for the saffron rule so it always sits in the
 * brand color regardless of theme.
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
      <svg
        width={size}
        height={size}
        viewBox="0 0 32 32"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-label="TutorBot"
        role="img"
        style={{ flexShrink: 0 }}
      >
        {/* Outer paper frame — warm bone, with subtle depth */}
        <rect
          x="2.5"
          y="3.5"
          width="27"
          height="25"
          rx="3.5"
          fill="currentColor"
          fillOpacity="0.08"
          stroke="currentColor"
          strokeOpacity="0.55"
          strokeWidth="1.2"
        />
        {/* Spine */}
        <line
          x1="16"
          y1="4.5"
          x2="16"
          y2="27.5"
          stroke="currentColor"
          strokeOpacity="0.4"
          strokeWidth="1"
        />
        {/* Left page lines */}
        <line x1="6" y1="10" x2="13" y2="10" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1" strokeLinecap="round" />
        <line x1="6" y1="14" x2="13" y2="14" stroke="currentColor" strokeOpacity="0.3" strokeWidth="1" strokeLinecap="round" />
        <line x1="6" y1="18" x2="13" y2="18" stroke="currentColor" strokeOpacity="0.3" strokeWidth="1" strokeLinecap="round" />
        {/* Right page — three "answer" lines, the last one a saffron accent */}
        <line x1="19" y1="10" x2="26" y2="10" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1" strokeLinecap="round" />
        <line x1="19" y1="14" x2="26" y2="14" stroke="currentColor" strokeOpacity="0.3" strokeWidth="1" strokeLinecap="round" />
        <line
          x1="19"
          y1="18"
          x2="24"
          y2="18"
          stroke="var(--color-brand-400)"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        {/* Tail — speech-bubble pointer bottom-right, hinting at conversation */}
        <path
          d="M22 27.5 L25.5 27.5 L23.5 30.2 Z"
          fill="var(--color-brand-400)"
          fillOpacity="0.85"
        />
      </svg>

      {showWordmark && (
        <div className="leading-none flex items-baseline gap-1.5">
          <span
            className={cn(
              "font-display font-semibold tracking-tight",
              wordmarkSize === "lg" ? "text-[17px]" : "text-sm",
            )}
          >
            TutorBot
          </span>
          <span
            className={cn(
              "font-mono-tab uppercase text-fg-subtle",
              wordmarkSize === "lg" ? "text-[9px] mt-0.5" : "text-[8px]",
            )}
            style={{ letterSpacing: "0.18em" }}
          >
            v1
          </span>
        </div>
      )}
    </div>
  );
}
