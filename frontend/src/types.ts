// Shapes returned by the backend (backend/aperture_api/queries.py, tools.py).
import type { Geometry, LineString, MultiPolygon, Polygon } from "geojson";

export type Tz = "UTC" | "ET";
export type Layer = "airports" | "ports" | "government" | "military";
export type Fence = Polygon | MultiPolygon;
export type BBox = [number, number, number, number];

export interface Health {
  status: string;
  loaded_at?: string;
  counts: Record<string, number>;
  time_range: [string, string];
}

/** [t (epoch s), icao24, lon, lat, alt_ft, track_deg, gs_kt, callsign] */
export type ReplayRow = [
  number,
  string,
  number,
  number,
  number | null,
  number | null,
  number | null,
  string | null,
];

export interface Replay {
  t0: string;
  t1: string;
  step_s: number;
  n_aircraft: number;
  columns: string[];
  rows: ReplayRow[];
}

export type AircraftFlags = Record<"military" | "interesting" | "pia" | "ladd", string[]>;

export interface AircraftSummary {
  kind: "aircraft";
  id: string;
  label: string;
  icao24: string;
  registration: string | null;
  type_code: string | null;
  description: string | null;
  owner_operator: string | null;
  year: string | null;
  category: string | null;
  military: boolean;
  interesting: boolean;
  pia: boolean;
  ladd: boolean;
  callsigns: string[];
  n_points: number;
  n_points_in_aoi: number;
}

export interface AircraftDetail extends AircraftSummary {
  legs: { leg: number; callsign: string | null; start: string; end: string; n_points: number; bbox: BBox }[];
}

export interface FeatureSummary {
  kind: "feature";
  id: number;
  label: string;
  layer: Layer;
  feature_kind: string;
  name: string | null;
  osm: string;
  icao: string | null;
  iata: string | null;
  centroid: [number, number];
}

export interface FeatureDetail extends FeatureSummary {
  tags: Record<string, string>;
  bbox: BBox;
  area_m2: number;
  geometry: Geometry;
}

/** A search hit in the "flights" group: an aircraft with its leg count. */
export interface FlightResult extends AircraftSummary {
  n_legs: number;
  first_seen: string | null;
}

export type SearchGroup = "flights" | Layer;

export interface SearchResponse {
  q: string;
  groups: { flights?: FlightResult[] } & Partial<Record<Layer, FeatureSummary[]>>;
}

export interface TrackLeg {
  leg: number;
  callsign: string | null;
  start: string;
  end: string;
  n_points: number;
  geometry: LineString;
  t: number[];
  alt_ft: (number | null)[];
}

export interface Track {
  icao24: string;
  start: string | null;
  end: string | null;
  legs: TrackLeg[];
}

export interface GeofenceHit {
  icao24: string;
  leg: number;
  callsign: string | null;
  registration: string | null;
  type_code: string | null;
  description: string | null;
  owner_operator: string | null;
  military: boolean;
  ladd: boolean;
  pia: boolean;
  entry: string;
  exit: string;
  duration_s: number;
  n_points_inside: number;
  min_alt_ft: number | null;
  max_alt_ft: number | null;
  on_ground: boolean;
  leg_geometry?: LineString;
}

export interface GeofenceResult {
  fence: Fence;
  start: string;
  end: string;
  n_hits: number;
  n_aircraft: number;
  hits: GeofenceHit[];
}

export type MapAction =
  | { type: "show_track"; icao24: string; label: string; legs: TrackLeg[] }
  | { type: "show_geofence_hits"; fence: Fence; start: string; end: string; hits: GeofenceHit[] }
  | { type: "highlight_features"; features: { id: number; layer: Layer; name: string | null; centroid: [number, number] }[] }
  | { type: "fit_bounds"; bbox: BBox };

export interface ToolTrace {
  name: string;
  arguments: unknown;
  ok: boolean;
  error?: string;
  map_actions?: string[];
}

export interface ChatReply {
  reply: string;
  map_actions: MapAction[];
  tool_calls: ToolTrace[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  tools?: ToolTrace[];
  /** Local error/stop notice: shown, never sent back to the model. */
  error?: boolean;
}

/** data/snapshot/MANIFEST.json, served by the edge as /provenance.json. */
export interface Provenance {
  adsb: { release: string; repo: string; license: string; date: string; selection: string };
  osm: { license: string; sources: { url: string; replication_timestamp: string }[] };
  basemap: { source: string; license: string };
}
