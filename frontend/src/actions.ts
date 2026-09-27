// User-level operations shared by several components.
import { api } from "./api";
import { REPLAY_BOUNDS } from "./playback";
import { hitKey, store } from "./store";
import { parseIso } from "./time";
import type { BBox, ChatMessage, FeatureDetail, Fence, GeofenceHit, Layer, TrackLeg } from "./types";

export async function init(): Promise<void> {
  try {
    const [health, flags] = await Promise.all([api.health(), api.flags()]);
    const day: [number, number] = [parseIso(health.time_range[0]), parseIso(health.time_range[1])];
    // Start mid-morning local time (busy, daylight) rather than at 00:00 UTC.
    store.set({ day, t: day[0] + 14 * 3600, flags, error: null });
  } catch (e) {
    store.set({ error: `cannot reach the API: ${String(e)}` });
  }
}

function ringsOf(fence: Fence): number[][][] {
  return fence.type === "Polygon" ? fence.coordinates : fence.coordinates.flat();
}

export function bboxOf(coords: number[][]): BBox {
  const xs = coords.map((c) => c[0]);
  const ys = coords.map((c) => c[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/** Every pass through the fence over the whole loaded day. */
export async function runGeofence(fence: Fence): Promise<void> {
  const day = store.get().day;
  if (!day) return;
  store.set({ fenceBusy: true, fenceError: null, selectedHit: null, tab: "geofence" });
  try {
    const r = await api.geofence(fence, day[0], day[1]);
    store.set({ fence: { fence: r.fence, source: "drawn", start: parseIso(r.start), end: parseIso(r.end), hits: r.hits } });
    store.fit(bboxOf(ringsOf(fence)[0]));
  } catch (e) {
    store.set({ fenceError: String(e) });
  } finally {
    store.set({ fenceBusy: false });
  }
}

export function clearFence(): void {
  store.set({ fence: null, selectedHit: null, fenceError: null, drawing: false });
}

let selectSeq = 0;

export async function selectAircraft(icao24: string | null): Promise<void> {
  const seq = ++selectSeq;
  store.set({ selected: icao24, selectedDetail: null, selectedTrack: null });
  if (!icao24) return;
  closePlace(); // one card at a time
  try {
    const [detail, track] = await Promise.all([api.aircraft(icao24), api.track(icao24)]);
    if (seq === selectSeq) store.set({ selectedDetail: detail, selectedTrack: track.legs });
  } catch (e) {
    if (seq === selectSeq) store.set({ error: `aircraft ${icao24}: ${String(e)}` });
  }
}

/**
 * The part of a day track that replay can show (inside `bounds`): when the aircraft first
 * appears there, and the extent of those points. Legs often begin or end far outside the AOI.
 */
export function trackInView(legs: readonly TrackLeg[], bounds: BBox): { t: number; bbox: BBox } | null {
  const [bx0, by0, bx1, by1] = bounds;
  let first = Infinity;
  const inside: number[][] = [];
  for (const leg of legs) {
    leg.geometry.coordinates.forEach(([x, y], i) => {
      if (x < bx0 || x > bx1 || y < by0 || y > by1) return;
      inside.push([x, y]);
      first = Math.min(first, leg.t[i]);
    });
  }
  return inside.length ? { t: first, bbox: bboxOf(inside) } : null;
}

/**
 * A flight search result: open its card and track, zoom to the track and jump to the first
 * moment the aircraft is on the map (play/pause unchanged).
 */
export async function openFlight(icao24: string): Promise<void> {
  await selectAircraft(icao24);
  const { selected, selectedTrack: legs } = store.get();
  if (selected !== icao24 || !legs?.length) return;
  const view = trackInView(legs, REPLAY_BOUNDS);
  store.seek(view ? view.t : parseIso(legs[0].start));
  store.fit(view ? view.bbox : bboxOf(legs.flatMap((l) => l.geometry.coordinates)));
}

export type PlaceRef = { id: number } | { layer: Layer; osmType: string; osmId: number };

let placeSeq = 0;

/** Open the place card for an infrastructure feature (search result or map click) and zoom to it. */
export async function selectPlace(ref: PlaceRef): Promise<void> {
  const seq = ++placeSeq;
  void selectAircraft(null); // one card at a time
  try {
    const place: FeatureDetail =
      "id" in ref ? await api.feature(ref.id) : await api.featureByOsm(ref.layer, ref.osmType, ref.osmId);
    if (seq !== placeSeq) return;
    store.set({ place });
    store.fit(place.bbox);
  } catch (e) {
    if (seq === placeSeq) store.set({ error: `place: ${String(e)}` });
  }
}

export function closePlace(): void {
  placeSeq++;
  if (store.get().place) store.set({ place: null });
}

/** Every pass through a buffer around a feature over the whole loaded day (Geofence tab). */
export async function geofencePlace(place: FeatureDetail, bufferM: number): Promise<void> {
  const day = store.get().day;
  if (!day) return;
  store.set({ fenceBusy: true, fenceError: null, selectedHit: null, tab: "geofence", drawing: false });
  try {
    const r = await api.geofenceFeature(place.id, bufferM, day[0], day[1]);
    store.set({
      fence: {
        fence: r.fence,
        source: "place",
        around: { name: place.label, osm: place.osm, buffer_m: bufferM },
        start: parseIso(r.start),
        end: parseIso(r.end),
        hits: r.hits,
      },
    });
    store.fit(bboxOf(ringsOf(r.fence).flat()));
  } catch (e) {
    store.set({ fenceError: String(e) });
  } finally {
    store.set({ fenceBusy: false });
  }
}

export function selectHit(hit: GeofenceHit | null): void {
  if (!hit) {
    store.set({ selectedHit: null });
    return;
  }
  store.set({ selectedHit: hitKey(hit), playing: false, tab: "geofence" });
  store.seek(parseIso(hit.entry));
  if (hit.leg_geometry) store.fit(bboxOf(hit.leg_geometry.coordinates));
  if (store.get().selected !== hit.icao24) void selectAircraft(hit.icao24);
}

let chatAbort: AbortController | null = null;

export async function sendChat(text: string): Promise<void> {
  const s = store.get();
  const content = text.trim();
  if (!content || s.chatPending) return;
  const history: ChatMessage[] = [...s.chat, { role: "user", content }];
  chatAbort = new AbortController();
  store.set({ chat: history, chatPending: true });
  try {
    const r = await api.chat(history.filter((m) => !m.error), s.tz, chatAbort.signal);
    store.set((st) => ({ chat: [...st.chat, { role: "assistant", content: r.reply, tools: r.tool_calls }] }));
    store.applyMapActions(r.map_actions);
  } catch (e) {
    const aborted = chatAbort?.signal.aborted;
    store.set((st) => ({
      chat: [...st.chat, { role: "assistant", content: aborted ? "_Stopped._" : `Error: ${String(e)}`, error: true }],
    }));
  } finally {
    chatAbort = null;
    store.set({ chatPending: false });
  }
}

export function stopChat(): void {
  chatAbort?.abort();
}

export function clearChat(): void {
  stopChat();
  store.set({ chat: [], tracks: [], highlights: [] });
}
