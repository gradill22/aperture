import { describe, expect, it } from "vitest";
import { trackInView } from "./actions";
import { flatten, formatKind } from "./components/Search";
import { parseBufferKm } from "./components/PlaceCard";
import type { BBox, FeatureSummary, FlightResult, SearchResponse, TrackLeg } from "./types";

const flight = { kind: "aircraft", id: "a00929", icao24: "a00929" } as FlightResult;
const place = (id: number, layer: FeatureSummary["layer"]) => ({ kind: "feature", id, layer }) as FeatureSummary;
const res: SearchResponse = {
  q: "x",
  groups: { flights: [flight], airports: [place(1, "airports"), place(2, "airports")], military: [place(3, "military")] },
};

describe("search", () => {
  it("flattens groups in chip order, skipping disabled or missing ones", () => {
    expect(flatten(res, ["flights", "airports", "ports", "military"]).map((i) => `${i.group}:${i.r.id}`)).toEqual([
      "flights:a00929",
      "airports:1",
      "airports:2",
      "military:3",
    ]);
    expect(flatten(res, ["military"]).map((i) => i.r.id)).toEqual([3]);
    expect(flatten(null, ["flights"])).toEqual([]);
  });

  it("accepts buffers in (0, 50] km", () => {
    expect(parseBufferKm("1")).toBe(1);
    expect(parseBufferKm("0.25")).toBe(0.25);
    expect(parseBufferKm("50")).toBe(50);
    for (const bad of ["", " ", "0", "-1", "50.1", "abc"]) expect(parseBufferKm(bad)).toBeNull();
  });
});

describe("flight results", () => {
  const leg = (t: number[], coordinates: number[][]) => ({ t, geometry: { type: "LineString", coordinates } }) as TrackLeg;
  const bounds: BBox = [-78, 38, -76, 40];

  it("jumps to the first point inside the replay bounds and fits to those points", () => {
    const legs = [
      leg([100, 110, 120], [[-74.7, 40.06], [-76.5, 39.5], [-77, 39]]), // starts near Trenton
      leg([500, 510], [[-77.2, 38.8], [-80, 38]]),
    ];
    expect(trackInView(legs, bounds)).toEqual({ t: 110, bbox: [-77.2, 38.8, -76.5, 39.5] });
    expect(trackInView([leg([1], [[-70, 40]])], bounds)).toBeNull();
  });

  it("formats OSM kinds", () => {
    expect(formatKind("aeroway=aerodrome")).toBe("aerodrome");
    expect(formatKind("military=naval_base")).toBe("naval base");
    expect(formatKind("heliport")).toBe("heliport");
  });
});
