// Time helpers. Everything internal is epoch seconds (UTC); only display depends on the tz toggle.
import type { Tz } from "./types";

const ZONES: Record<Tz, string> = { UTC: "UTC", ET: "America/New_York" };
const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(tz: Tz, withDate: boolean, withSeconds: boolean): Intl.DateTimeFormat {
  const key = `${tz}|${withDate}|${withSeconds}`;
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-CA", {
      timeZone: ZONES[tz],
      hourCycle: "h23",
      hour: "2-digit",
      minute: "2-digit",
      ...(withSeconds ? { second: "2-digit" } : {}),
      ...(withDate ? { year: "numeric", month: "2-digit", day: "2-digit" } : {}),
      timeZoneName: "short",
    });
    formatters.set(key, f);
  }
  return f;
}

function parts(epochS: number, tz: Tz, withDate: boolean, withSeconds: boolean) {
  const out: Record<string, string> = {};
  for (const p of formatter(tz, withDate, withSeconds).formatToParts(new Date(epochS * 1000))) {
    out[p.type] = p.value;
  }
  return out;
}

/** Zone abbreviation at that instant: "UTC", "EDT" or "EST". */
export function zoneName(epochS: number, tz: Tz): string {
  if (tz === "UTC") return "UTC";
  const name = parts(epochS, tz, false, false).timeZoneName;
  // Some ICU builds render America/New_York as GMT-4 / GMT-5.
  return name === "GMT-4" ? "EDT" : name === "GMT-5" ? "EST" : name;
}

/** "14:05" or "14:05:38" in the display zone (no zone suffix). */
export function formatClock(epochS: number, tz: Tz, withSeconds = true): string {
  const p = parts(epochS, tz, false, withSeconds);
  return withSeconds ? `${p.hour}:${p.minute}:${p.second}` : `${p.hour}:${p.minute}`;
}

/** "2026-09-24 14:05:38 UTC" / "2026-09-24 10:05:38 EDT". */
export function formatDateTime(epochS: number, tz: Tz): string {
  const p = parts(epochS, tz, true, true);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} ${zoneName(epochS, tz)}`;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(r).padStart(2, "0")}s`;
  return `${r}s`;
}

export const parseIso = (iso: string): number => Date.parse(iso) / 1000;

/** Second-precision ISO string with Z, e.g. 2026-09-24T14:00:00Z. */
export const toIso = (epochS: number): string =>
  new Date(Math.floor(epochS) * 1000).toISOString().replace(".000Z", "Z");
