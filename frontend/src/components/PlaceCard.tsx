import { useState } from "react";
import { closePlace, geofencePlace } from "../actions";
import { useStore } from "../store";
import { LAYER_COLORS } from "../style";
import { formatKind } from "./Search";

export const DEFAULT_BUFFER_KM = 1;
export const MAX_BUFFER_KM = 50; // the API's limit (buffer_m <= 50 000)

const CODE_TAGS: [string, string][] = [
  ["icao", "ICAO"],
  ["iata", "IATA"],
  ["faa", "FAA"],
  ["ref", "Ref"],
];

export function parseBufferKm(value: string): number | null {
  const km = Number(value);
  return value.trim() !== "" && Number.isFinite(km) && km > 0 && km <= MAX_BUFFER_KM ? km : null;
}

/** Infrastructure feature details, opened from search or a map click. */
export function PlaceCard() {
  const place = useStore((s) => s.place);
  const busy = useStore((s) => s.fenceBusy);
  const [km, setKm] = useState(String(DEFAULT_BUFFER_KM));
  if (!place) return null;

  const bufferKm = parseBufferKm(km);
  const codes = CODE_TAGS.filter(([k]) => place.tags[k]).map(([k, label]) => `${label} ${place.tags[k]}`);
  const operator = place.tags.operator;

  return (
    <div className="aircraft-card place-card" data-testid="place-card">
      <button className="close" aria-label="Close" onClick={closePlace}>
        ×
      </button>
      <h3>
        <span className="swatch" style={{ background: LAYER_COLORS[place.layer] }} /> {place.label}
      </h3>
      <p className="muted">
        {place.layer} · {formatKind(place.feature_kind)}
      </p>
      {codes.length > 0 && <p>{codes.join(" · ")}</p>}
      {operator && <p>{operator}</p>}
      {place.area_m2 > 0 && <p className="muted">Area {(place.area_m2 / 1e6).toLocaleString("en-US", { maximumFractionDigits: 2 })} km²</p>}
      <p className="muted">
        <code>OSM {place.osm}</code>
      </p>
      <form
        className="place-fence"
        noValidate // parseBufferKm decides; step=0.1 is only the spinner's increment
        onSubmit={(e) => {
          e.preventDefault();
          if (bufferKm !== null) void geofencePlace(place, bufferKm * 1000);
        }}
      >
        <label>
          Buffer{" "}
          <input
            type="number"
            aria-label="Buffer (km)"
            data-testid="place-buffer"
            min={0.1}
            max={MAX_BUFFER_KM}
            step={0.1}
            value={km}
            onChange={(e) => setKm(e.target.value)}
          />{" "}
          km
        </label>
        <button
          type="submit"
          className="primary"
          data-testid="place-geofence"
          disabled={bufferKm === null || busy}
          title="Find every aircraft that entered this buffer during the day"
        >
          Geofence
        </button>
      </form>
    </div>
  );
}
