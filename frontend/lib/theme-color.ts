/**
 * Resolve a TutorBot theme CSS variable for libraries that cannot parse
 * CSS custom properties (mermaid's khroma color parser throws
 * ``Unsupported color format`` on ``rgb(var(--color-*))``).
 *
 * Our theme variables are space-separated RGB triplets
 * (``--color-bg-subtle: 255 226 197``). khroma accepts the comma form
 * ``rgb(255,226,197)``, so we read the computed value and reformat it.
 * Falls back to a literal color when the variable is missing or
 * malformed (e.g. SSR / test environments without the stylesheet).
 */
export function resolveThemeColor(name: string, fallback: string): string {
  try {
    if (typeof document === "undefined") return fallback;
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    const channels = raw.split(/\s+/);
    if (
      channels.length === 3 &&
      channels.every((channel) => /^\d{1,3}$/.test(channel))
    ) {
      return `rgb(${channels.join(",")})`;
    }
    return fallback;
  } catch {
    return fallback;
  }
}
