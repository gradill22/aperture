import { describe, expect, it } from "vitest";
import { findingFilename, findingMarkdown, type FindingInput } from "./export";
import { parseIso } from "./time";
import type { GeofenceHit, Provenance } from "./types";

const hit: GeofenceHit = {
  icao24: "a00929",
  leg: 3,
  callsign: "HQ101",
  registration: "N101HQ",
  type_code: "EC45",
  description: "Airbus Helicopters EC145",
  owner_operator: "Example Operator",
  military: false,
  ladd: true,
  pia: false,
  entry: "2026-09-24T14:05:00Z",
  exit: "2026-09-24T14:12:30Z",
  duration_s: 450,
  n_points_inside: 91,
  min_alt_ft: 0,
  max_alt_ft: 1200,
  on_ground: true,
  leg_geometry: { type: "LineString", coordinates: [[-77.04, 38.85], [-77.03, 38.86]] },
};

const provenance: Provenance = {
  adsb: { release: "v2026.09.24-planes-readsb-prod-0", repo: "adsblol/globe_history_2026", license: "ODbL 1.0, adsb.lol", date: "2026-09-24", selection: "full-day trace" },
  osm: { license: "ODbL 1.0", sources: [{ url: "x/district-of-columbia-latest.osm.pbf", replication_timestamp: "2026-09-26T20:22:51Z" }] },
  basemap: { source: "20260924.pmtiles", license: "ODbL" },
};

const input = (over: Partial<FindingInput> = {}): FindingInput => ({
  hit,
  aircraft: null,
  fence: { type: "Polygon", coordinates: [[[-77.05, 38.84], [-77.02, 38.84], [-77.02, 38.87], [-77.05, 38.84]]] },
  fenceSource: "drawn",
  window: [parseIso("2026-09-24T00:00:00Z"), parseIso("2026-09-25T00:00:00Z")],
  note: "Loitered over the river.",
  tz: "UTC",
  provenance,
  generatedAt: parseIso("2026-09-27T12:00:00Z"),
  ...over,
});

describe("finding export", () => {
  it("has identity, times, note, fence and provenance", () => {
    const md = findingMarkdown(input());
    expect(md).toMatch(/^# Finding: N101HQ \(a00929\) crossed geofence/);
    expect(md).toContain("**Entry:** 2026-09-24 14:05:00 UTC");
    expect(md).toContain("**Time inside:** 7m 30s (91 position reports)");
    expect(md).toContain("reported on ground inside the fence");
    expect(md).toContain("| Registry flags | LADD |");
    expect(md).toContain("Loitered over the river.");
    expect(md).toContain('"type":"Polygon"');
    expect(md).toContain('"type":"LineString"');
    expect(md).toContain("release `v2026.09.24-planes-readsb-prod-0`");
    expect(md).toContain("district-of-columbia-latest.osm.pbf @ 2026-09-26T20:22:51Z");
  });

  it("shows ET with UTC alongside", () => {
    expect(findingMarkdown(input({ tz: "ET" }))).toContain(
      "**Entry:** 2026-09-24 10:05:00 EDT (2026-09-24 14:05:00 UTC)",
    );
  });

  it("degrades gracefully without note or manifest", () => {
    const md = findingMarkdown(input({ note: "  ", provenance: null }));
    expect(md).toContain("_No note._");
    expect(md).toContain("Snapshot manifest unavailable.");
  });

  it("names the file after the aircraft and entry time", () => {
    expect(findingFilename(input())).toBe("finding-a00929-20260924-1405Z.md");
  });
});
