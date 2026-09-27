import { describe, expect, it } from "vitest";
import { isTextEntry, shortcutFor } from "./keys";

const el = (tagName: string, extra: Record<string, unknown> = {}) => ({ tagName, getAttribute: () => null, ...extra });
const key = (k: string, target: unknown = el("DIV"), mods: Partial<KeyboardEvent> = {}) =>
  shortcutFor({ key: k, target: target as EventTarget, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods });

describe("keyboard shortcuts", () => {
  it("maps arrows, space and slash", () => {
    expect(key("ArrowLeft")).toBe("back");
    expect(key("ArrowRight")).toBe("ahead");
    expect(key(" ")).toBe("play");
    expect(key("/")).toBe("search");
    expect(key("a")).toBeNull();
    expect(key("ArrowLeft", null)).toBe("back");
  });

  it("never fires while typing", () => {
    for (const t of [el("INPUT", { type: "search" }), el("INPUT", { type: "number" }), el("TEXTAREA"), el("SELECT"), el("DIV", { isContentEditable: true })]) {
      expect(isTextEntry(t as unknown as EventTarget)).toBe(true);
      expect(key(" ", t)).toBeNull();
      expect(key("/", t)).toBeNull();
      expect(key("ArrowLeft", t)).toBeNull();
    }
  });

  it("wins over non-text widgets (map, scrubber, buttons, checkboxes)", () => {
    for (const t of [el("CANVAS"), el("INPUT", { type: "range" }), el("INPUT", { type: "checkbox" }), el("BUTTON")]) {
      expect(isTextEntry(t as unknown as EventTarget)).toBe(false);
      expect(key("ArrowRight", t)).toBe("ahead");
      expect(key(" ", t)).toBe("play");
    }
  });

  it("leaves modified keys and the splitter's arrows alone", () => {
    expect(key("ArrowLeft", el("DIV"), { shiftKey: true })).toBeNull();
    expect(key("ArrowLeft", el("DIV"), { ctrlKey: true })).toBeNull();
    expect(key(" ", el("DIV"), { metaKey: true })).toBeNull();
    const splitter = el("DIV", { getAttribute: (n: string) => (n === "role" ? "separator" : null) });
    expect(key("ArrowLeft", splitter)).toBeNull();
    expect(key("ArrowRight", splitter)).toBeNull();
    expect(key(" ", splitter)).toBe("play");
  });
});
