// Drives the playhead and keeps the replay buffer filled around it.
import aoi from "../../data/aoi.json";
import { api } from "./api";
import { ReplayBuffer, chunksAround } from "./replay";
import type { Store } from "./store";

export const CHUNK_S = 900; // one /replay request = 15 min
export const STEP_S = 10; // bucket width
export const MAX_GAP_S = 120; // interpolate across gaps up to this; beyond, the aircraft blinks out
export const HOLD_S = 30; // keep the last fix on screen this long after the final sample
export const TRAIL_S = 90;
const BEHIND_S = 900;
const AHEAD_S = 1800;
const KEEP_BEHIND_S = 2700;
const KEEP_AHEAD_S = 3600;
const PAD_DEG = 0.3; // replay aircraft slightly beyond the AOI so they fly in, not pop in

const [x0, y0, x1, y1] = aoi.bbox;
export const REPLAY_BBOX = [x0 - PAD_DEG, y0 - PAD_DEG, x1 + PAD_DEG, y1 + PAD_DEG].map((v) => v.toFixed(4)).join(",");

export class Playback {
  readonly buffer = new ReplayBuffer();
  /** Bumped whenever the buffer gains data, so the map knows to redraw while paused. */
  version = 0;
  private loaded = new Set<number>();
  private inflight = new Map<number, AbortController>();
  private raf = 0;
  private last = 0;
  private listeners = new Set<() => void>();
  private readonly store: Store;

  constructor(store: Store) {
    this.store = store;
  }

  onData(l: () => void): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  start(): () => void {
    const unsub = this.store.subscribe(() => this.sync());
    this.sync();
    return () => {
      unsub();
      if (this.raf) cancelAnimationFrame(this.raf);
      for (const c of this.inflight.values()) c.abort();
    };
  }

  private playing = false;

  private sync(): void {
    const s = this.store.get();
    if (!s.day) return;
    this.ensure(s.t, s.day);
    if (s.playing && !this.playing) {
      this.playing = true;
      this.last = performance.now();
      this.raf = requestAnimationFrame(this.tick);
    } else if (!s.playing && this.playing) {
      this.playing = false;
      cancelAnimationFrame(this.raf);
    }
  }

  private tick = (now: number): void => {
    const s = this.store.get();
    if (!s.playing || !s.day) return;
    const dt = Math.min(0.25, (now - this.last) / 1000); // cap after a background-tab stall
    this.last = now;
    const t = s.t + dt * s.speed;
    if (t >= s.day[1]) {
      this.store.set({ t: s.day[1], playing: false });
      return;
    }
    // Don't run ahead of the data: wait (paused in place) while the next chunk loads.
    const chunk = Math.floor((t - s.day[0]) / CHUNK_S);
    if (this.loaded.has(chunk)) this.store.set({ t });
    this.raf = requestAnimationFrame(this.tick);
  };

  private ensure(t: number, day: [number, number]): void {
    const want = chunksAround(t, CHUNK_S, BEHIND_S, AHEAD_S, day);
    for (const c of want) {
      if (this.loaded.has(c) || this.inflight.has(c)) continue;
      this.fetch(c, day);
    }
    // Abort requests the playhead has moved away from (fast scrubbing).
    const wanted = new Set(want);
    for (const [c, ctl] of this.inflight) {
      if (!wanted.has(c)) {
        ctl.abort();
        this.inflight.delete(c);
      }
    }
    const lo = t - KEEP_BEHIND_S;
    const hi = t + KEEP_AHEAD_S;
    const stale = [...this.loaded].filter((c) => {
      const c0 = day[0] + c * CHUNK_S;
      return c0 + CHUNK_S < lo || c0 > hi;
    });
    if (stale.length) {
      for (const c of stale) this.loaded.delete(c);
      const keep = [...this.loaded];
      if (keep.length) {
        this.buffer.retain(day[0] + Math.min(...keep) * CHUNK_S, day[0] + (Math.max(...keep) + 1) * CHUNK_S);
      } else {
        this.buffer.retain(0, 0);
      }
    }
    this.setLoading();
  }

  /** Only write on change: every store write re-enters sync(). */
  private setLoading(): void {
    const loading = this.inflight.size > 0;
    if (this.store.get().loading !== loading) this.store.set({ loading });
  }

  private fetch(c: number, day: [number, number]): void {
    const ctl = new AbortController();
    this.inflight.set(c, ctl);
    const t0 = day[0] + c * CHUNK_S;
    const t1 = Math.min(day[1], t0 + CHUNK_S);
    api
      .replay(t0, t1, STEP_S, REPLAY_BBOX, ctl.signal)
      .then((r) => {
        if (ctl.signal.aborted) return;
        this.buffer.add(r.rows);
        this.loaded.add(c);
        this.version++;
        for (const l of this.listeners) l();
      })
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) this.store.set({ error: `replay ${c}: ${String(e)}` });
      })
      .finally(() => {
        if (this.inflight.get(c) === ctl) this.inflight.delete(c);
        this.setLoading();
      });
  }
}
