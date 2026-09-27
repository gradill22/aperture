import { describe, expect, it } from "vitest";
import { ReplayBuffer, bearing, chunksAround, interpolate, lastAtOrBefore, lerpAngle, type Sample } from "./replay";
import type { ReplayRow } from "./types";

const s = (t: number, lon: number, lat: number, track: number | null = 90, alt: number | null = 1000): Sample => ({
  t,
  lon,
  lat,
  alt,
  track,
  gs: 100,
  callsign: "TEST1",
});

describe("lastAtOrBefore", () => {
  const xs = [s(10, 0, 0), s(20, 0, 0), s(30, 0, 0)];
  it.each([
    [5, -1],
    [10, 0],
    [15, 0],
    [20, 1],
    [29.9, 1],
    [30, 2],
    [99, 2],
  ])("t=%s -> %s", (at, idx) => expect(lastAtOrBefore(xs, at)).toBe(idx));
  it("empty", () => expect(lastAtOrBefore([], 1)).toBe(-1));
});

describe("lerpAngle", () => {
  it("takes the short way across north", () => {
    expect(lerpAngle(350, 10, 0.5)).toBeCloseTo(0);
    expect(lerpAngle(10, 350, 0.25)).toBeCloseTo(5);
  });
  it("plain interpolation", () => expect(lerpAngle(90, 180, 0.5)).toBeCloseTo(135));
});

describe("bearing", () => {
  it("cardinal directions", () => {
    expect(bearing(0, 0, 0, 1)).toBeCloseTo(0);
    expect(bearing(0, 0, 1, 0)).toBeCloseTo(90);
    expect(bearing(0, 0, 0, -1)).toBeCloseTo(180);
    expect(bearing(0, 0, -1, 0)).toBeCloseTo(270);
  });
});

describe("interpolate", () => {
  const xs = [s(0, -77, 38), s(10, -76.9, 38.1), s(500, -76, 39)];
  it("is linear between close samples", () => {
    const p = interpolate(xs, 5, 120, 30)!;
    expect(p.lon).toBeCloseTo(-76.95);
    expect(p.lat).toBeCloseTo(38.05);
  });
  it("holds the last fix across a long gap, then drops it", () => {
    expect(interpolate(xs, 30, 120, 30)!.lon).toBe(-76.9);
    expect(interpolate(xs, 41, 120, 30)).toBeNull();
  });
  it("is null before the first sample", () => expect(interpolate(xs, -1, 120, 30)).toBeNull());
  it("falls back to the bearing when track is missing", () => {
    const p = interpolate([s(0, 0, 0, null), s(10, 0, 1, null)], 5, 120, 30)!;
    expect(p.track).toBeCloseTo(0);
  });
  it("tolerates null altitude on one side", () => {
    const p = interpolate([s(0, 0, 0, 0, null), s(10, 0, 1, 0, 2000)], 5, 120, 30)!;
    expect(p.alt).toBe(2000);
  });
});

describe("ReplayBuffer", () => {
  const row = (t: number, id: string, lon = 0): ReplayRow => [t, id, lon, 0, 100, 0, 50, null];

  it("merges unsorted, overlapping chunks without duplicates", () => {
    const b = new ReplayBuffer();
    b.add([row(20, "a"), row(0, "a"), row(10, "b")]);
    b.add([row(10, "a"), row(20, "a")]);
    expect(b.tracks.get("a")!.map((x) => x.t)).toEqual([0, 10, 20]);
    expect(b.tracks.get("b")!.length).toBe(1);
  });

  it("statesAt and retain", () => {
    const b = new ReplayBuffer();
    b.add([row(0, "a", 0), row(10, "a", 1), row(100, "b", 5)]);
    expect(b.statesAt(5, 120, 30).map((x) => [x.icao24, x.lon])).toEqual([["a", 0.5]]);
    b.retain(50, 200);
    expect([...b.tracks.keys()]).toEqual(["b"]);
  });

  it("trail ends at the interpolated position and stops at gaps", () => {
    const b = new ReplayBuffer();
    b.add([row(0, "a", 0), row(200, "a", 1), row(210, "a", 2), row(220, "a", 3)]);
    expect(b.trail("a", 215, 180, 120)).toEqual([
      [1, 0],
      [2, 0],
      [2.5, 0],
    ]);
  });
});

describe("chunksAround", () => {
  const day: [number, number] = [0, 86400];
  it("clamps to the day", () => {
    expect(chunksAround(0, 900, 900, 1800, day)).toEqual([0, 1, 2]);
    expect(chunksAround(86399, 900, 900, 1800, day)).toEqual([94, 95]);
  });
  it("covers behind and ahead", () => expect(chunksAround(4500, 900, 900, 1800, day)).toEqual([4, 5, 6, 7]));
});
