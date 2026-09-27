import { useSyncExternalStore } from "react";
import { selectAircraft } from "../actions";
import { HOLD_S, MAX_GAP_S, type Playback } from "../playback";
import { interpolate } from "../replay";
import { store, useStore } from "../store";
import { formatClock, parseIso } from "../time";
import { Flags } from "./GeofencePanel";

export function AircraftCard({ playback }: { playback: Playback }) {
  const selected = useStore((s) => s.selected);
  const detail = useStore((s) => s.selectedDetail);
  const t = useStore((s) => s.t);
  const tz = useStore((s) => s.tz);
  useSyncExternalStore(
    (l) => playback.onData(l),
    () => playback.version,
  );
  if (!selected) return null;

  const samples = playback.buffer.tracks.get(selected);
  const now = samples ? interpolate(samples, t, MAX_GAP_S, HOLD_S) : null;

  return (
    <div className="aircraft-card" data-testid="aircraft-card">
      <button className="close" aria-label="Close" onClick={() => void selectAircraft(null)}>
        ×
      </button>
      <h3>
        {detail?.registration ?? selected} {detail && <Flags h={detail} />}
      </h3>
      <p className="muted">
        <code>{selected}</code>
        {detail?.type_code ? ` · ${detail.type_code}` : ""}
      </p>
      {detail?.description && <p>{detail.description}</p>}
      {detail?.owner_operator && <p>{detail.owner_operator}</p>}
      <p>
        {now
          ? `${now.callsign ?? "—"} · ${now.alt === null ? "alt —" : now.alt <= 0 ? "on ground" : `${Math.round(now.alt)} ft`} · ${now.gs === null ? "— kt" : `${Math.round(now.gs)} kt`}`
          : "Not broadcasting at this time"}
      </p>
      {detail && (
        <ul className="legs">
          {detail.legs.map((l) => (
            <li key={l.leg}>
              <button onClick={() => store.seek(parseIso(l.start))} title="Jump to leg start">
                {formatClock(parseIso(l.start), tz, false)}–{formatClock(parseIso(l.end), tz, false)}
              </button>{" "}
              {l.callsign ?? ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
