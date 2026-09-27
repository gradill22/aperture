// User-level operations shared by several components.
import { api } from "./api";
import { hitKey, store } from "./store";
import { parseIso } from "./time";
import type { BBox, ChatMessage, Fence, GeofenceHit } from "./types";

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
  try {
    const [detail, track] = await Promise.all([api.aircraft(icao24), api.track(icao24)]);
    if (seq === selectSeq) store.set({ selectedDetail: detail, selectedTrack: track.legs });
  } catch (e) {
    if (seq === selectSeq) store.set({ error: `aircraft ${icao24}: ${String(e)}` });
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
