import { selectHit } from "../actions";
import { SPEEDS, hitKey, store, useStore } from "../store";
import { formatClock, parseIso, zoneName } from "../time";

const TICK_EVERY_S = 3 * 3600;

export function Timeline() {
  const day = useStore((s) => s.day);
  const t = useStore((s) => s.t);
  const tz = useStore((s) => s.tz);
  const playing = useStore((s) => s.playing);
  const speed = useStore((s) => s.speed);
  const loading = useStore((s) => s.loading);
  const fence = useStore((s) => s.fence);
  const selectedHit = useStore((s) => s.selectedHit);

  if (!day) return <div className="timeline" />;
  const span = day[1] - day[0];
  const pct = (at: number) => `${(((at - day[0]) / span) * 100).toFixed(3)}%`;
  const hourTicks: number[] = [];
  for (let at = day[0]; at <= day[1]; at += TICK_EVERY_S) hourTicks.push(at);

  return (
    <div className="timeline" data-testid="timeline">
      <div className="timeline-controls">
        <button
          className="play"
          data-testid="play"
          aria-label={playing ? "Pause" : "Play"}
          onClick={() => store.set({ playing: !playing && t < day[1] })}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <div className="speeds" role="group" aria-label="Playback speed">
          {SPEEDS.map((v) => (
            <button key={v} className={v === speed ? "on" : ""} onClick={() => store.set({ speed: v })}>
              {v}×
            </button>
          ))}
        </div>
        <div className="clock" data-testid="clock">
          {formatClock(t, tz)} <span className="zone">{zoneName(t, tz)}</span>
        </div>
        {loading && <span className="loading">loading tracks…</span>}
        {fence && (
          <span className="hit-legend">
            <span className="tick-swatch" /> {fence.hits.length} geofence passes
          </span>
        )}
      </div>
      <div className="scrubber">
        <input
          type="range"
          aria-label="Replay time"
          data-testid="scrubber"
          min={day[0]}
          max={day[1]}
          step={1}
          value={t}
          onChange={(e) => store.seek(Number(e.target.value))}
        />
        <div className="hit-ticks">
          {fence?.hits.map((h) => {
            const key = hitKey(h);
            const entry = parseIso(h.entry);
            const exit = parseIso(h.exit);
            return (
              <button
                key={key}
                className={`hit-tick${key === selectedHit ? " sel" : ""}${h.military ? " mil" : ""}`}
                style={{ left: pct(entry), width: `max(3px, ${pct(day[0] + (exit - entry))})` }}
                title={`${h.registration ?? h.callsign ?? h.icao24} ${formatClock(entry, tz, false)}–${formatClock(exit, tz, false)}`}
                onClick={() => selectHit(h)}
              />
            );
          })}
        </div>
        <div className="hour-ticks" aria-hidden>
          {hourTicks.map((at) => (
            <span key={at} style={{ left: pct(at) }}>
              {formatClock(at, tz, false)}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
