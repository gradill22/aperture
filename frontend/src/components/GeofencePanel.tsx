import { useEffect, useRef } from "react";
import { clearFence, selectHit } from "../actions";
import { hitKey, store, useStore } from "../store";
import { formatClock, formatDateTime, formatDuration, parseIso } from "../time";
import type { GeofenceHit } from "../types";
import { FindingExport } from "./FindingExport";

export function Flags({ h }: { h: Pick<GeofenceHit, "military" | "ladd" | "pia"> }) {
  return (
    <>
      {h.military && <span className="badge mil">MIL</span>}
      {h.ladd && <span className="badge priv">LADD</span>}
      {h.pia && <span className="badge priv">PIA</span>}
    </>
  );
}

const alt = (lo: number | null, hi: number | null) =>
  lo === null && hi === null ? "alt —" : `${lo ?? "—"}–${hi ?? "—"} ft`;

export function GeofencePanel() {
  const fence = useStore((s) => s.fence);
  const drawing = useStore((s) => s.drawing);
  const busy = useStore((s) => s.fenceBusy);
  const error = useStore((s) => s.fenceError);
  const selectedHit = useStore((s) => s.selectedHit);
  const tz = useStore((s) => s.tz);
  const day = useStore((s) => s.day);
  const listRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    listRef.current?.querySelector(".hit.sel")?.scrollIntoView({ block: "nearest" });
  }, [selectedHit]);

  const hits = fence ? [...fence.hits].sort((a, b) => a.entry.localeCompare(b.entry)) : [];
  const nAircraft = new Set(hits.map((h) => h.icao24)).size;

  return (
    <div className="panel geofence" data-testid="geofence-panel">
      <div className="panel-actions">
        <button
          className={drawing ? "primary on" : "primary"}
          data-testid="draw-fence"
          disabled={!day || busy}
          onClick={() => store.set({ drawing: !drawing, playing: false })}
        >
          {drawing ? "Cancel drawing" : fence ? "Draw new geofence" : "Draw geofence"}
        </button>
        {fence && (
          <button onClick={clearFence} data-testid="clear-fence">
            Clear
          </button>
        )}
      </div>
      {drawing && (
        <p className="hint">
          Click the map to add corners. Click the first corner (or double-click) to finish. Esc cancels.
        </p>
      )}
      {busy && <p className="hint">Searching the whole day…</p>}
      {error && <p className="error">{error}</p>}
      {!fence && !drawing && !busy && (
        <p className="hint">
          Draw a polygon to find every aircraft that crossed it during the loaded day. You can also ask the chat,
          e.g. “Which aircraft passed within 2 km of Reagan National between 14:00 and 15:00?”
        </p>
      )}
      {fence && (
        <>
          <p className="summary" data-testid="fence-summary">
            <strong>{hits.length}</strong> passes by <strong>{nAircraft}</strong> aircraft
            <br />
            <span className="muted">
              {formatDateTime(fence.start, tz)} → {formatDateTime(fence.end, tz)}
              {fence.source === "chat" ? " · from chat" : ""}
            </span>
          </p>
          <ol className="hits" ref={listRef} data-testid="hits">
            {hits.map((h) => {
              const key = hitKey(h);
              const sel = key === selectedHit;
              return (
                <li key={key} className={sel ? "hit sel" : "hit"}>
                  <button className="hit-row" onClick={() => selectHit(sel ? null : h)} aria-expanded={sel}>
                    <span className="hit-time">{formatClock(parseIso(h.entry), tz, false)}</span>
                    <span className="hit-id">
                      <strong>{h.registration ?? h.callsign ?? h.icao24}</strong>
                      {h.callsign && h.registration && <span className="muted"> {h.callsign}</span>}
                      <Flags h={h} />
                      <br />
                      <span className="muted">
                        {h.type_code ?? "type ?"} · {formatDuration(h.duration_s)} · {alt(h.min_alt_ft, h.max_alt_ft)}
                        {h.on_ground ? " · on ground" : ""}
                      </span>
                    </span>
                  </button>
                  {sel && <FindingExport hit={h} />}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </div>
  );
}
