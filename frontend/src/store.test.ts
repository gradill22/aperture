import { describe, expect, it } from "vitest";
import { Store } from "./store";
import { parseIso } from "./time";
import type { GeofenceHit, TrackLeg } from "./types";

const day: [number, number] = [parseIso("2026-09-24T00:00:00Z"), parseIso("2026-09-25T00:00:00Z")];
const leg: TrackLeg = {
  leg: 1,
  callsign: null,
  start: "2026-09-24T10:00:00Z",
  end: "2026-09-24T11:00:00Z",
  n_points: 2,
  geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] },
  t: [0, 1],
  alt_ft: [0, 0],
};

describe("Store", () => {
  it("clamps seek to the day", () => {
    const s = new Store({ day });
    s.seek(day[0] - 100);
    expect(s.get().t).toBe(day[0]);
    s.seek(day[1] + 100);
    expect(s.get().t).toBe(day[1]);
  });

  it("applies chat map actions", () => {
    const s = new Store({ day });
    const hit = { icao24: "abc123", leg: 1, entry: "2026-09-24T14:05:00Z" } as GeofenceHit;
    s.applyMapActions([
      { type: "show_track", icao24: "abc123", label: "N1", legs: [leg] },
      { type: "show_track", icao24: "abc123", label: "N1", legs: [leg] },
      {
        type: "show_geofence_hits",
        fence: { type: "Polygon", coordinates: [] },
        start: "2026-09-24T14:00:00Z",
        end: "2026-09-24T15:00:00Z",
        hits: [hit],
      },
      { type: "highlight_features", features: [{ id: 1, layer: "airports", name: "X", centroid: [0, 0] }] },
      { type: "fit_bounds", bbox: [0, 0, 1, 1] },
      { type: "fit_bounds", bbox: [0, 0, 2, 2] },
    ]);
    const st = s.get();
    expect(st.tracks).toHaveLength(1);
    expect(st.fence?.source).toBe("chat");
    expect(st.fence?.hits).toEqual([hit]);
    expect(st.t).toBe(parseIso(hit.entry));
    expect(st.highlights).toHaveLength(1);
    expect(st.fitRequest).toEqual({ bbox: [0, 0, 2, 2], seq: 2 });
  });

  it("notifies subscribers", () => {
    const s = new Store();
    let n = 0;
    const off = s.subscribe(() => n++);
    s.set({ speed: 10 });
    off();
    s.set({ speed: 1 });
    expect(n).toBe(1);
  });
});
