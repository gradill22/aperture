import { afterEach, describe, expect, it, vi } from "vitest";
import { CHUNK_S, Playback } from "./playback";
import { Store } from "./store";

const day: [number, number] = [1_790_208_000, 1_790_294_400];

afterEach(() => vi.unstubAllGlobals());

describe("Playback", () => {
  it("fetches the chunks around the playhead once, without re-entrant store loops", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      const t0 = decodeURIComponent(url.split("t0=")[1].split("&")[0]);
      const t = Date.parse(t0) / 1000;
      return new Response(JSON.stringify({ rows: [[t, "abc123", -77, 38.9, 1000, 90, 120, "TEST1"]] }));
    });
    const store = new Store();
    const pb = new Playback(store);
    let writes = 0;
    store.subscribe(() => writes++);
    const stop = pb.start();
    store.set({ day, t: day[0] + 4 * CHUNK_S + 10 });
    await vi.waitFor(() => expect(store.get().loading).toBe(false));
    // chunk 4 (behind 900 s -> 3) .. ahead 1800 s -> 6
    expect(urls).toHaveLength(4);
    expect(urls.every((u) => u.startsWith("/api/replay?") && u.includes("&bbox="))).toBe(true);
    expect(writes).toBeLessThan(20);
    expect(pb.version).toBe(4);
    expect(pb.buffer.tracks.get("abc123")).toHaveLength(4);
    // Moving within the loaded window fetches nothing new.
    store.seek(day[0] + 4 * CHUNK_S + 300);
    expect(urls).toHaveLength(4);
    stop();
  });
});
