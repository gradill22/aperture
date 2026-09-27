// Side panel (chat / geofence) width: user-adjustable, remembered per browser.

export const DEFAULT_WIDTH = 400;
export const MIN_WIDTH = 300;
export const MAX_WIDTH = 900;
export const MAP_MIN_WIDTH = 360; // the map always keeps at least this much
export const STEP = 16;
export const BIG_STEP = 64;
const KEY = "aperture.asideWidth";

/** The widest the panel may be in a viewport this wide (never below MIN_WIDTH). */
export function maxWidth(viewport: number): number {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, viewport - MAP_MIN_WIDTH));
}

export function clampWidth(width: number, viewport: number): number {
  if (!Number.isFinite(width)) return Math.min(DEFAULT_WIDTH, maxWidth(viewport));
  return Math.round(Math.min(maxWidth(viewport), Math.max(MIN_WIDTH, width)));
}

// Storage can be unavailable (private mode, blocked site data): the default width still works.
export function loadWidth(): number {
  try {
    const v = Number(localStorage.getItem(KEY));
    return v > 0 ? v : DEFAULT_WIDTH;
  } catch {
    return DEFAULT_WIDTH;
  }
}

export function saveWidth(width: number): void {
  try {
    localStorage.setItem(KEY, String(width));
  } catch {
    // not persisted; fine
  }
}
