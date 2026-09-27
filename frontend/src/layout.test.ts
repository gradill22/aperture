import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_WIDTH, MAX_WIDTH, MIN_WIDTH, clampWidth, loadWidth, maxWidth, saveWidth } from "./layout";

describe("clampWidth", () => {
  it("keeps widths inside [300, min(900, viewport - 360)]", () => {
    expect(clampWidth(400, 1440)).toBe(400);
    expect(clampWidth(100, 1440)).toBe(MIN_WIDTH);
    expect(clampWidth(2000, 1440)).toBe(MAX_WIDTH); // 1440 - 360 = 1080, capped at 900
    expect(clampWidth(2000, 2560)).toBe(MAX_WIDTH);
    expect(clampWidth(800, 1000)).toBe(640); // the map keeps 360 px
    expect(clampWidth(412.6, 1440)).toBe(413);
  });

  it("never goes below the minimum, even in a narrow window", () => {
    expect(maxWidth(500)).toBe(MIN_WIDTH);
    expect(clampWidth(800, 500)).toBe(MIN_WIDTH);
  });

  it("falls back to the default for junk", () => {
    expect(clampWidth(NaN, 1440)).toBe(DEFAULT_WIDTH);
    expect(clampWidth(Infinity, 1440)).toBe(DEFAULT_WIDTH);
  });
});

describe("persistence", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("round-trips through localStorage", () => {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    });
    expect(loadWidth()).toBe(DEFAULT_WIDTH);
    saveWidth(520);
    expect(loadWidth()).toBe(520);
  });

  it("ignores stored junk", () => {
    vi.stubGlobal("localStorage", { getItem: () => "wide", setItem: () => {} });
    expect(loadWidth()).toBe(DEFAULT_WIDTH);
  });

  it("works when storage throws (blocked site data)", () => {
    const boom = () => {
      throw new DOMException("denied", "SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: boom, setItem: boom });
    expect(loadWidth()).toBe(DEFAULT_WIDTH);
    expect(() => saveWidth(500)).not.toThrow();
  });
});
