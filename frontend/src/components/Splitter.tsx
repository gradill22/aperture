import { useRef } from "react";
import { BIG_STEP, DEFAULT_WIDTH, MIN_WIDTH, STEP, maxWidth } from "../layout";

type Props = {
  /** Current side panel width in px (already clamped). */
  width: number;
  onChange: (width: number) => void;
};

/** Vertical divider between the map and the side panel. Drag it, or focus it and use the arrows;
 *  double-click restores the default width. Its value is the side panel's width. */
export function Splitter({ width, onChange }: Props) {
  const drag = useRef<{ x: number; width: number } | null>(null);
  const max = maxWidth(window.innerWidth);

  return (
    <div
      className="splitter"
      data-testid="splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize side panel"
      aria-controls="side-panel"
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={max}
      aria-valuenow={width}
      aria-valuetext={`${width} px`}
      tabIndex={0}
      title="Drag to resize (double-click to reset)"
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault(); // no text selection while dragging
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, width };
        document.body.classList.add("resizing");
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        // The panel is on the right: moving the divider left widens it.
        if (d) onChange(d.width - (e.clientX - d.x));
      }}
      onPointerUp={(e) => {
        drag.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
        document.body.classList.remove("resizing");
      }}
      onLostPointerCapture={() => {
        drag.current = null;
        document.body.classList.remove("resizing");
      }}
      onDoubleClick={() => onChange(DEFAULT_WIDTH)}
      onKeyDown={(e) => {
        const step = e.shiftKey ? BIG_STEP : STEP;
        const next = {
          ArrowLeft: width + step,
          ArrowRight: width - step,
          Home: MIN_WIDTH,
          End: max,
        }[e.key];
        if (next === undefined) return;
        e.preventDefault();
        onChange(next);
      }}
    />
  );
}
