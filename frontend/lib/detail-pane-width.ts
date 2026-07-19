/**
 * Right detail-pane width (2026-07-19 plan): state-driven, clamped,
 * persisted to localStorage (same pattern as ``tutor:theme``).
 */

export const DETAIL_WIDTH_STORAGE_KEY = "tutor:detailPaneWidth";
export const DETAIL_WIDTH_DEFAULT = 520;
export const DETAIL_WIDTH_MIN = 320;
export const DETAIL_WIDTH_MAX = 760;

export function clampDetailWidth(width: number): number {
  if (!Number.isFinite(width)) return DETAIL_WIDTH_DEFAULT;
  return Math.min(
    DETAIL_WIDTH_MAX,
    Math.max(DETAIL_WIDTH_MIN, Math.round(width)),
  );
}

export function readDetailWidth(): number {
  try {
    if (typeof window === "undefined") return DETAIL_WIDTH_DEFAULT;
    const raw = window.localStorage.getItem(DETAIL_WIDTH_STORAGE_KEY);
    if (raw === null) return DETAIL_WIDTH_DEFAULT;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return DETAIL_WIDTH_DEFAULT;
    return clampDetailWidth(parsed);
  } catch {
    return DETAIL_WIDTH_DEFAULT;
  }
}

export function writeDetailWidth(width: number): void {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(
      DETAIL_WIDTH_STORAGE_KEY,
      String(clampDetailWidth(width)),
    );
  } catch {
    // localStorage may be blocked (private mode); keep the in-memory value.
  }
}
