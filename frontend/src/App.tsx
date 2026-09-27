import { useEffect, useState } from "react";
import { init } from "./actions";
import { AircraftCard } from "./components/AircraftCard";
import { ChatPanel } from "./components/ChatPanel";
import { GeofencePanel } from "./components/GeofencePanel";
import { MapView } from "./components/MapView";
import { Timeline } from "./components/Timeline";
import { Playback } from "./playback";
import { LAYERS, store, useStore } from "./store";
import { LAYER_COLORS } from "./style";
import type { Tz } from "./types";

function LayerToggles() {
  const layers = useStore((s) => s.layers);
  const trails = useStore((s) => s.trails);
  const labels = useStore((s) => s.labels);
  return (
    <fieldset className="layer-toggles" data-testid="layers">
      <legend>Layers</legend>
      {LAYERS.map((l) => (
        <label key={l}>
          <input
            type="checkbox"
            checked={layers[l]}
            onChange={(e) => store.set({ layers: { ...layers, [l]: e.target.checked } })}
          />
          <span className="swatch" style={{ background: LAYER_COLORS[l] }} />
          {l}
        </label>
      ))}
      <label>
        <input type="checkbox" checked={trails} onChange={(e) => store.set({ trails: e.target.checked })} />
        trails
      </label>
      <label>
        <input type="checkbox" checked={labels} onChange={(e) => store.set({ labels: e.target.checked })} />
        callsigns
      </label>
    </fieldset>
  );
}

function Header() {
  const tz = useStore((s) => s.tz);
  const day = useStore((s) => s.day);
  const error = useStore((s) => s.error);
  return (
    <header>
      <h1>Aperture</h1>
      <span className="muted">
        Washington DC · 50 mi · {day ? new Date(day[0] * 1000).toISOString().slice(0, 10) : "…"}
      </span>
      <span className="badge offline" title="All data and models are served from this machine">
        offline
      </span>
      {error && (
        <span className="error" role="alert">
          {error}{" "}
          <button onClick={() => store.set({ error: null })} aria-label="Dismiss">
            ×
          </button>
        </span>
      )}
      <div className="spacer" />
      <div className="tz" role="group" aria-label="Display time zone">
        {(["UTC", "ET"] as Tz[]).map((z) => (
          <button key={z} className={z === tz ? "on" : ""} onClick={() => store.set({ tz: z })}>
            {z}
          </button>
        ))}
      </div>
    </header>
  );
}

export function App() {
  const [playback] = useState(() => new Playback(store));
  const tab = useStore((s) => s.tab);
  const nHits = useStore((s) => s.fence?.hits.length);

  useEffect(() => {
    void init();
    return playback.start();
  }, [playback]);

  return (
    <div className="app">
      <Header />
      <main>
        <div className="map-wrap">
          <MapView playback={playback} />
          <LayerToggles />
          <AircraftCard playback={playback} />
        </div>
        <aside>
          <nav className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "chat"} onClick={() => store.set({ tab: "chat" })}>
              Chat
            </button>
            <button
              role="tab"
              data-testid="tab-geofence"
              aria-selected={tab === "geofence"}
              onClick={() => store.set({ tab: "geofence" })}
            >
              Geofence{nHits !== undefined ? ` (${nHits})` : ""}
            </button>
          </nav>
          {tab === "chat" ? <ChatPanel /> : <GeofencePanel />}
        </aside>
        <Timeline />
      </main>
    </div>
  );
}
