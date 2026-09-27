// Replay buffer: /api/replay rows grouped per aircraft, interpolated to any instant.
import type { ReplayRow } from "./types";

export interface Sample {
  t: number;
  lon: number;
  lat: number;
  alt: number | null;
  track: number | null;
  gs: number | null;
  callsign: string | null;
}

export interface AircraftState {
  icao24: string;
  lon: number;
  lat: number;
  alt: number | null;
  track: number;
  gs: number | null;
  callsign: string | null;
}

/** Index of the last sample with t <= at, or -1. Samples must be sorted by t. */
export function lastAtOrBefore(samples: readonly Sample[], at: number): number {
  let lo = 0;
  let hi = samples.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].t <= at) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** Interpolate an angle in degrees along the shorter arc. */
export function lerpAngle(a: number, b: number, f: number): number {
  const d = ((((b - a) % 360) + 540) % 360) - 180;
  return (((a + d * f) % 360) + 360) % 360;
}

const lerp = (a: number, b: number, f: number) => a + (b - a) * f;
const lerpNullable = (a: number | null, b: number | null, f: number) =>
  a === null || b === null ? (a ?? b) : lerp(a, b, f);

/** Bearing from one lon/lat to another, degrees clockwise from north (for missing track). */
export function bearing(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const r = Math.PI / 180;
  const y = Math.sin((lon2 - lon1) * r) * Math.cos(lat2 * r);
  const x =
    Math.cos(lat1 * r) * Math.sin(lat2 * r) - Math.sin(lat1 * r) * Math.cos(lat2 * r) * Math.cos((lon2 - lon1) * r);
  return ((Math.atan2(y, x) / r) % 360 + 360) % 360;
}

/**
 * Position at `at`. Between two samples no more than `maxGapS` apart the position is linearly
 * interpolated; after the last sample it is held for `holdS`, then the aircraft disappears.
 */
export function interpolate(samples: readonly Sample[], at: number, maxGapS: number, holdS: number) {
  const i = lastAtOrBefore(samples, at);
  if (i < 0) return null;
  const a = samples[i];
  const b = samples[i + 1];
  if (!b || b.t - a.t > maxGapS) {
    return at - a.t <= holdS ? { ...a, track: a.track ?? 0 } : null;
  }
  const f = (at - a.t) / (b.t - a.t);
  const track =
    a.track !== null && b.track !== null
      ? lerpAngle(a.track, b.track, f)
      : (a.track ?? b.track ?? bearing(a.lon, a.lat, b.lon, b.lat));
  return {
    t: at,
    lon: lerp(a.lon, b.lon, f),
    lat: lerp(a.lat, b.lat, f),
    alt: lerpNullable(a.alt, b.alt, f),
    track,
    gs: lerpNullable(a.gs, b.gs, f),
    callsign: a.callsign ?? b.callsign,
  };
}

export class ReplayBuffer {
  readonly tracks = new Map<string, Sample[]>();

  /** Merge rows (any order, possibly overlapping earlier chunks). */
  add(rows: readonly ReplayRow[]): void {
    const touched = new Set<string>();
    for (const [t, icao24, lon, lat, alt, track, gs, callsign] of rows) {
      let list = this.tracks.get(icao24);
      if (!list) {
        list = [];
        this.tracks.set(icao24, list);
      }
      list.push({ t, lon, lat, alt, track, gs, callsign });
      touched.add(icao24);
    }
    for (const icao24 of touched) {
      const list = this.tracks.get(icao24)!;
      list.sort((x, y) => x.t - y.t);
      // Chunk boundaries can repeat a bucket: keep one sample per timestamp.
      let w = 0;
      for (let r = 0; r < list.length; r++) {
        if (w === 0 || list[r].t !== list[w - 1].t) list[w++] = list[r];
      }
      list.length = w;
    }
  }

  /** Drop samples outside [t0, t1) (memory bound while scrubbing a whole day). */
  retain(t0: number, t1: number): void {
    for (const [icao24, list] of this.tracks) {
      const kept = list.filter((s) => s.t >= t0 && s.t < t1);
      if (kept.length) this.tracks.set(icao24, kept);
      else this.tracks.delete(icao24);
    }
  }

  statesAt(at: number, maxGapS: number, holdS: number): AircraftState[] {
    const out: AircraftState[] = [];
    for (const [icao24, list] of this.tracks) {
      const s = interpolate(list, at, maxGapS, holdS);
      if (s) out.push({ icao24, lon: s.lon, lat: s.lat, alt: s.alt, track: s.track, gs: s.gs, callsign: s.callsign });
    }
    return out;
  }

  /** Recent path ending at the interpolated position, for short trails. */
  trail(icao24: string, at: number, trailS: number, maxGapS: number): [number, number][] {
    const list = this.tracks.get(icao24);
    if (!list) return [];
    const end = lastAtOrBefore(list, at);
    const coords: [number, number][] = [];
    for (let i = end; i >= 0 && at - list[i].t <= trailS; i--) {
      if (i < end && list[i + 1].t - list[i].t > maxGapS) break;
      coords.push([list[i].lon, list[i].lat]);
    }
    coords.reverse();
    const now = interpolate(list, at, maxGapS, 0);
    if (now) coords.push([now.lon, now.lat]);
    return coords;
  }
}

/** Which fixed-size chunks cover [at - behind, at + ahead]. */
export function chunksAround(at: number, chunkS: number, behind: number, ahead: number, day: [number, number]) {
  const first = Math.floor((Math.max(day[0], at - behind) - day[0]) / chunkS);
  const last = Math.floor((Math.min(day[1] - 1, at + ahead) - day[0]) / chunkS);
  const out: number[] = [];
  for (let c = first; c <= last; c++) out.push(c);
  return out;
}
