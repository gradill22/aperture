import { describe, expect, it } from "vitest";
import { formatClock, formatDateTime, formatDuration, parseIso, toIso, zoneName } from "./time";

const t = parseIso("2026-09-24T14:05:38Z");

describe("time", () => {
  it("round-trips ISO", () => {
    expect(toIso(t)).toBe("2026-09-24T14:05:38Z");
    expect(toIso(t + 0.9)).toBe("2026-09-24T14:05:38Z");
  });
  it("formats UTC", () => {
    expect(formatDateTime(t, "UTC")).toBe("2026-09-24 14:05:38 UTC");
    expect(formatClock(t, "UTC", false)).toBe("14:05");
  });
  it("formats ET with DST awareness", () => {
    expect(formatDateTime(t, "ET")).toBe("2026-09-24 10:05:38 EDT");
    expect(zoneName(parseIso("2026-12-24T14:00:00Z"), "ET")).toBe("EST");
    expect(formatClock(parseIso("2026-09-24T02:30:00Z"), "ET")).toBe("22:30:00");
  });
  it("formats durations", () => {
    expect(formatDuration(42)).toBe("42s");
    expect(formatDuration(125)).toBe("2m 05s");
    expect(formatDuration(3 * 3600 + 7 * 60)).toBe("3h 07m");
  });
});
