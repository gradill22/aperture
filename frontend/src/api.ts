// Same-origin API client. The edge (nginx) proxies /api/* to the backend.
import { toIso } from "./time";
import type {
  AircraftDetail,
  AircraftFlags,
  ChatMessage,
  ChatReply,
  FeatureDetail,
  Fence,
  GeofenceResult,
  Health,
  Layer,
  Provenance,
  Replay,
  SearchGroup,
  SearchResponse,
  Track,
  Tz,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    detail: string,
  ) {
    super(detail);
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const get = <T>(path: string, signal?: AbortSignal) => fetch(`/api${path}`, { signal }).then(json<T>);

const post = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  }).then(json<T>);

export const api = {
  health: () => get<Health>("/health"),
  replay: (t0: number, t1: number, stepS: number, bbox: string, signal?: AbortSignal) =>
    get<Replay>(`/replay?t0=${toIso(t0)}&t1=${toIso(t1)}&step=${stepS}&bbox=${bbox}`, signal),
  flags: () => get<AircraftFlags>("/aircraft/flags"),
  aircraft: (icao24: string) => get<AircraftDetail>(`/entities/aircraft/${encodeURIComponent(icao24)}`),
  search: (q: string, groups: SearchGroup[], signal?: AbortSignal) =>
    get<SearchResponse>(
      `/search?${new URLSearchParams([["q", q], ...groups.map((g) => ["groups", g]), ["per_group", "6"]])}`,
      signal,
    ),
  feature: (id: number) => get<FeatureDetail>(`/entities/feature/${id}`),
  /** Map clicks: vector tiles carry the OSM identity, not the feature id. */
  featureByOsm: (layer: Layer, osmType: string, osmId: number) =>
    get<FeatureDetail>(`/entities/feature/osm/${layer}/${encodeURIComponent(osmType)}/${osmId}`),
  track: (icao24: string) => get<Track>(`/tracks/${encodeURIComponent(icao24)}`),
  geofence: (fence: Fence, start: number, end: number) =>
    post<GeofenceResult>("/geofence", { polygon: fence, start: toIso(start), end: toIso(end) }),
  geofenceFeature: (featureId: number, bufferM: number, start: number, end: number) =>
    post<GeofenceResult>("/geofence", { feature_id: featureId, buffer_m: bufferM, start: toIso(start), end: toIso(end) }),
  chat: (messages: ChatMessage[], tz: Tz, signal?: AbortSignal) =>
    post<ChatReply>(
      "/chat",
      { messages: messages.map(({ role, content }) => ({ role, content })), tz },
      signal,
    ),
  provenance: () => fetch("/provenance.json").then(json<Provenance>),
};
