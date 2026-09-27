// Global keyboard shortcuts: Left/Right skip, Space plays/pauses, "/" focuses the search box.
import type { Store } from "./store";

const NON_TEXT_INPUTS = new Set(["checkbox", "radio", "range", "button", "submit", "reset", "color", "file"]);

/** The parts of a focused element we look at (duck-typed, so the logic is testable without a DOM). */
interface KeyTarget {
  tagName?: string;
  type?: string;
  isContentEditable?: boolean;
  getAttribute?(name: string): string | null;
}

/** Typing into this element must never trigger a shortcut. */
export function isTextEntry(target: EventTarget | null): boolean {
  const el = target as KeyTarget | null;
  const tag = el?.tagName?.toUpperCase();
  if (!tag) return false;
  if (el!.isContentEditable || tag === "TEXTAREA" || tag === "SELECT") return true;
  return tag === "INPUT" && !NON_TEXT_INPUTS.has((el!.type ?? "text").toLowerCase());
}

export type Shortcut = "back" | "ahead" | "play" | "search";

export function shortcutFor(e: Pick<KeyboardEvent, "key" | "target" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">): Shortcut | null {
  if (e.ctrlKey || e.metaKey || e.altKey || isTextEntry(e.target)) return null;
  if (e.key === "/") return "search";
  if (e.shiftKey) return null; // Shift+arrows stay with the map (rotate/pitch)
  // The splitter uses the arrow keys itself (resize the panel).
  const onSeparator = (e.target as KeyTarget | null)?.getAttribute?.("role") === "separator";
  if (e.key === "ArrowLeft" && !onSeparator) return "back";
  if (e.key === "ArrowRight" && !onSeparator) return "ahead";
  if (e.key === " ") return "play";
  return null;
}

/**
 * Listen on window in the capture phase so the shortcut wins over focused widgets: MapLibre pans
 * on plain arrows, the scrubber steps 1 s, and Space would click a focused button.
 */
export function installShortcuts(store: Store, focusSearch: () => void): () => void {
  let swallowSpaceUp = false;
  const onDown = (e: KeyboardEvent) => {
    const action = shortcutFor(e);
    if (!action) return;
    e.preventDefault();
    e.stopPropagation();
    if (action === "search") focusSearch();
    else if (action === "back") store.skip(-1);
    else if (action === "ahead") store.skip(1);
    else {
      swallowSpaceUp = true;
      if (e.repeat) return;
      const { playing, t, day } = store.get();
      store.set({ playing: !playing && !!day && t < day[1] });
    }
  };
  // A focused button activates on Space keyup: don't let it also click.
  const onUp = (e: KeyboardEvent) => {
    if (e.key === " " && swallowSpaceUp) {
      swallowSpaceUp = false;
      e.preventDefault();
      e.stopPropagation();
    }
  };
  window.addEventListener("keydown", onDown, true);
  window.addEventListener("keyup", onUp, true);
  return () => {
    window.removeEventListener("keydown", onDown, true);
    window.removeEventListener("keyup", onUp, true);
  };
}
