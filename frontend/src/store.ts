// App state: one small external store read through useSyncExternalStore. The map subscribes
// imperatively (it redraws on `t` without re-rendering React).
import { useSyncExternalStore } from "react";
import { parseIso } from "./time";
import type {
  AircraftDetail,
  AircraftFlags,
  BBox,
  ChatMessage,
  FeatureDetail,
  Fence,
  GeofenceHit,
  Layer,
  MapAction,
  TrackLeg,
  Tz,
} from "./types";

export const SPEEDS = [1, 10, 60, 300] as const;
/** The skip buttons jump this many units, where a unit is one second of replay at 1× (5 s at 1×, 50 s at 10×). */
export const SKIP_UNITS = 5;
export const LAYERS: Layer[] = ["airports", "ports", "government", "military"];

export interface ShownTrack {
  icao24: string;
  label: string;
  legs: TrackLeg[];
}

/** The feature a place-card geofence was buffered around. */
export interface FenceAround {
  name: string;
  osm: string;
  buffer_m: number;
}

export interface FenceState {
  fence: Fence;
  source: "drawn" | "chat" | "place";
  around?: FenceAround;
  start: number;
  end: number;
  hits: GeofenceHit[];
}

export interface Highlight {
  id: number;
  layer: Layer;
  name: string | null;
  centroid: [number, number];
}

export interface State {
  tz: Tz;
  day: [number, number] | null;
  t: number;
  playing: boolean;
  speed: number;
  loading: boolean;
  layers: Record<Layer, boolean>;
  trails: boolean;
  labels: boolean;
  flags: AircraftFlags | null;
  drawing: boolean;
  fence: FenceState | null;
  fenceBusy: boolean;
  fenceError: string | null;
  /** Selected geofence pass, as `${icao24}:${leg}`. */
  selectedHit: string | null;
  /** Selected aircraft (map click or hit click), with its registry entry and day track. */
  selected: string | null;
  selectedDetail: AircraftDetail | null;
  selectedTrack: TrackLeg[] | null;
  /** Selected infrastructure feature (search result or map click). Exclusive with `selected`. */
  place: FeatureDetail | null;
  tab: "chat" | "geofence";
  tracks: ShownTrack[];
  highlights: Highlight[];
  chat: ChatMessage[];
  chatPending: boolean;
  fitRequest: { bbox: BBox; seq: number } | null;
  error: string | null;
}

const initial: State = {
  tz: "UTC",
  day: null,
  t: 0,
  playing: false,
  speed: 60,
  loading: false,
  layers: { airports: true, ports: true, government: true, military: true },
  trails: true,
  labels: true,
  flags: null,
  drawing: false,
  fence: null,
  fenceBusy: false,
  fenceError: null,
  selectedHit: null,
  selected: null,
  selectedDetail: null,
  selectedTrack: null,
  place: null,
  tab: "chat",
  tracks: [],
  highlights: [],
  chat: [],
  chatPending: false,
  fitRequest: null,
  error: null,
};

type Listener = () => void;

export class Store {
  private state: State;
  private listeners = new Set<Listener>();

  constructor(init: Partial<State> = {}) {
    this.state = { ...initial, ...init };
  }

  get = (): State => this.state;

  set = (patch: Partial<State> | ((s: State) => Partial<State>)): void => {
    const p = typeof patch === "function" ? patch(this.state) : patch;
    this.state = { ...this.state, ...p };
    for (const l of this.listeners) l();
  };

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };

  /** Clamp and move the playhead. */
  seek(t: number): void {
    const day = this.state.day;
    if (!day) return;
    this.set({ t: Math.min(day[1], Math.max(day[0], t)) });
  }

  /** Jump SKIP_UNITS units back (-1) or ahead (+1) at the current speed, keeping play/pause. */
  skip(direction: -1 | 1): void {
    const { day, t, speed } = this.state;
    if (!day) return;
    this.seek(t + direction * skipSeconds(speed));
    if (this.state.t >= day[1] && this.state.playing) this.set({ playing: false });
  }

  fit(bbox: BBox): void {
    this.set((s) => ({ fitRequest: { bbox, seq: (s.fitRequest?.seq ?? 0) + 1 } }));
  }

  /** Apply the map actions returned by /chat (see backend/aperture_api/tools.py). */
  applyMapActions(actions: readonly MapAction[]): void {
    for (const a of actions) {
      switch (a.type) {
        case "show_track":
          this.set((s) => ({
            tracks: [...s.tracks.filter((x) => x.icao24 !== a.icao24), { icao24: a.icao24, label: a.label, legs: a.legs }],
          }));
          break;
        case "show_geofence_hits": {
          const start = parseIso(a.start);
          this.set({
            fence: { fence: a.fence, source: "chat", start, end: parseIso(a.end), hits: a.hits },
            selectedHit: null,
            fenceError: null,
          });
          if (a.hits.length) this.seek(parseIso(a.hits[0].entry));
          else this.seek(start);
          break;
        }
        case "highlight_features":
          this.set({ highlights: a.features });
          break;
        case "fit_bounds":
          this.fit(a.bbox);
          break;
      }
    }
  }
}

export const skipSeconds = (speed: number): number => SKIP_UNITS * speed;

export const hitKey = (h: Pick<GeofenceHit, "icao24" | "leg">): string => `${h.icao24}:${h.leg}`;

export const store = new Store();

export function useStore<T>(select: (s: State) => T): T {
  return useSyncExternalStore(store.subscribe, () => select(store.get()));
}
