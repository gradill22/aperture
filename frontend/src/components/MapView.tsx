import {
  Map as MlMap,
  NavigationControl,
  ScaleControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapMouseEvent,
} from "maplibre-gl";
// MapLibre 6 ships its worker as a separate module; let Vite bundle it and hand over the URL.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { TerraDraw, TerraDrawPolygonMode } from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { useEffect, useRef } from "react";
import aoi from "../../../data/aoi.json";
import { runGeofence, selectAircraft, selectHit } from "../actions";
import { HOLD_S, MAX_GAP_S, TRAIL_S, type Playback } from "../playback";
import { hitKey, store, type State } from "../store";
import { INFRA_LAYERS, buildStyle, infraLayerIds } from "../style";
import type { Fence } from "../types";

setWorkerUrl(workerUrl);

const FENCE = "#2563eb";
const HIT = "#ea580c";
const TRACK = "#0f766e";
const REDRAW_MS = 50;

const fc = (features: Feature[]): FeatureCollection => ({ type: "FeatureCollection", features });
const feat = (geometry: Geometry, properties: Record<string, unknown> = {}): Feature => ({
  type: "Feature",
  geometry,
  properties,
});

/** Plane silhouette pointing north, as an SDF-able alpha mask (recolored per aircraft). */
function planeIcon(size: number): ImageData {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d")!;
  const s = size / 32;
  g.scale(s, s);
  g.fillStyle = "#000";
  g.beginPath();
  // fuselage + wings + tailplane
  g.moveTo(16, 2);
  g.bezierCurveTo(17.6, 2, 18, 4, 18, 6);
  g.lineTo(18, 12);
  g.lineTo(29, 18);
  g.lineTo(29, 21);
  g.lineTo(18, 18);
  g.lineTo(17.5, 25);
  g.lineTo(21, 28);
  g.lineTo(21, 30);
  g.lineTo(16, 28.6);
  g.lineTo(11, 30);
  g.lineTo(11, 28);
  g.lineTo(14.5, 25);
  g.lineTo(14, 18);
  g.lineTo(3, 21);
  g.lineTo(3, 18);
  g.lineTo(14, 12);
  g.lineTo(14, 6);
  g.bezierCurveTo(14, 4, 14.4, 2, 16, 2);
  g.closePath();
  g.fill();
  return g.getImageData(0, 0, size, size);
}

function addOverlays(map: MlMap): void {
  map.addImage("plane", planeIcon(64), { sdf: true, pixelRatio: 2 });
  for (const id of ["fence", "hit-legs", "tracks", "trails", "aircraft", "highlights"]) {
    map.addSource(id, { type: "geojson", data: fc([]) });
  }
  map.addLayer({ id: "fence-fill", type: "fill", source: "fence", paint: { "fill-color": FENCE, "fill-opacity": 0.08 } });
  map.addLayer({
    id: "fence-line",
    type: "line",
    source: "fence",
    paint: { "line-color": FENCE, "line-width": 2, "line-dasharray": [3, 2] },
  });
  map.addLayer({
    id: "hit-legs",
    type: "line",
    source: "hit-legs",
    layout: { "line-join": "round", "line-cap": "round", "line-sort-key": ["case", ["get", "sel"], 1, 0] },
    paint: {
      "line-color": HIT,
      "line-width": ["case", ["get", "sel"], 4, 1.5],
      "line-opacity": ["case", ["get", "sel"], 0.95, ["get", "dim"], 0.15, 0.45],
    },
  });
  map.addLayer({
    id: "tracks",
    type: "line",
    source: "tracks",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": ["case", ["==", ["get", "kind"], "selected"], "#475569", TRACK],
      "line-width": ["case", ["==", ["get", "kind"], "selected"], 1.5, 3],
      "line-dasharray": ["case", ["==", ["get", "kind"], "selected"], ["literal", [2, 2]], ["literal", [1, 0]]],
      "line-opacity": 0.85,
    },
  });
  map.addLayer({
    id: "trails",
    type: "line",
    source: "trails",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": "#64748b", "line-width": 1.2, "line-opacity": 0.55 },
  });
  map.addLayer({
    id: "highlights",
    type: "circle",
    source: "highlights",
    paint: {
      "circle-radius": 14,
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-color": "#f59e0b",
      "circle-stroke-width": 3,
    },
  });
  map.addLayer({
    id: "aircraft",
    type: "symbol",
    source: "aircraft",
    layout: {
      "icon-image": "plane",
      "icon-rotate": ["get", "track"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      // zoom must be the top-level input, so the selection case goes inside each stop.
      "icon-size": [
        "interpolate",
        ["linear"],
        ["zoom"],
        7,
        ["case", ["get", "sel"], 1.0, 0.55],
        12,
        ["case", ["get", "sel"], 1.4, 0.9],
      ],
      "symbol-sort-key": ["case", ["get", "sel"], 2, ["get", "mil"], 1, 0],
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 10,
      "text-offset": [0, 1.3],
      "text-anchor": "top",
      "text-optional": true,
    },
    paint: {
      "icon-color": [
        "case",
        ["get", "mil"],
        "#dc2626",
        ["get", "special"],
        "#9333ea",
        ["<=", ["coalesce", ["get", "alt"], 1], 0],
        "#78716c",
        "#1d4ed8",
      ],
      "icon-halo-color": ["case", ["get", "sel"], "#fde047", "#ffffff"],
      "icon-halo-width": ["case", ["get", "sel"], 3, 1],
      "text-color": "#1e293b",
      "text-halo-color": "#ffffff",
      "text-halo-width": 1.2,
    },
  });
}

function fenceGeometry(fence: Fence | undefined): Feature[] {
  return fence ? [feat(fence)] : [];
}

/** Everything but the moving aircraft: redrawn only when the relevant state changes. */
function drawStatic(map: MlMap, s: State, prev: State | null): void {
  const src = (id: string) => map.getSource(id) as GeoJSONSource;
  if (!prev || prev.fence !== s.fence || prev.selectedHit !== s.selectedHit) {
    src("fence").setData(fc(fenceGeometry(s.fence?.fence)));
    const legs = (s.fence?.hits ?? [])
      .filter((h) => h.leg_geometry)
      .map((h) =>
        feat(h.leg_geometry!, {
          key: hitKey(h),
          icao24: h.icao24,
          sel: hitKey(h) === s.selectedHit,
          dim: s.selectedHit !== null && hitKey(h) !== s.selectedHit,
        }),
      );
    src("hit-legs").setData(fc(legs));
  }
  if (!prev || prev.tracks !== s.tracks || prev.selectedTrack !== s.selectedTrack) {
    const lines = s.tracks.flatMap((tr) => tr.legs.map((l) => feat(l.geometry, { icao24: tr.icao24, kind: "chat" })));
    for (const l of s.selectedTrack ?? []) lines.push(feat(l.geometry, { icao24: s.selected, kind: "selected" }));
    src("tracks").setData(fc(lines));
  }
  if (!prev || prev.highlights !== s.highlights) {
    src("highlights").setData(
      fc(s.highlights.map((h) => feat({ type: "Point", coordinates: h.centroid }, { id: h.id, name: h.name }))),
    );
  }
  if (!prev || prev.layers !== s.layers) {
    for (const layer of INFRA_LAYERS) {
      for (const id of infraLayerIds(layer)) map.setLayoutProperty(id, "visibility", s.layers[layer] ? "visible" : "none");
    }
  }
  if (!prev || prev.labels !== s.labels) {
    map.setLayoutProperty("aircraft", "text-field", s.labels ? ["step", ["zoom"], "", 9, ["get", "label"]] : "");
  }
  if (!prev || prev.trails !== s.trails) {
    map.setLayoutProperty("trails", "visibility", s.trails ? "visible" : "none");
  }
  if (s.fitRequest && prev?.fitRequest?.seq !== s.fitRequest.seq) {
    const [x0, y0, x1, y1] = s.fitRequest.bbox;
    map.fitBounds(
      [
        [x0, y0],
        [x1, y1],
      ],
      { padding: 60, maxZoom: 13, duration: 600 },
    );
  }
}

function drawAircraft(map: MlMap, playback: Playback, s: State, container: HTMLElement): void {
  const states = playback.buffer.statesAt(s.t, MAX_GAP_S, HOLD_S);
  const mil = new Set(s.flags?.military ?? []);
  const special = new Set([...(s.flags?.ladd ?? []), ...(s.flags?.pia ?? []), ...(s.flags?.interesting ?? [])]);
  const features = states.map((a) =>
    feat(
      { type: "Point", coordinates: [a.lon, a.lat] },
      {
        icao24: a.icao24,
        track: a.track,
        alt: a.alt,
        label: a.callsign ?? a.icao24,
        mil: mil.has(a.icao24),
        special: special.has(a.icao24),
        sel: a.icao24 === s.selected,
      },
    ),
  );
  (map.getSource("aircraft") as GeoJSONSource).setData(fc(features));
  const trails = s.trails
    ? states
        .map((a) => playback.buffer.trail(a.icao24, s.t, TRAIL_S, MAX_GAP_S))
        .filter((c) => c.length > 1)
        .map((coordinates) => feat({ type: "LineString", coordinates }))
    : [];
  (map.getSource("trails") as GeoJSONSource).setData(fc(trails));
  container.dataset.aircraftCount = String(features.length);
}

declare global {
  interface Window {
    __aperture?: { map: MlMap; store: typeof store; playback: Playback };
  }
}

export function MapView({ playback }: { playback: Playback }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = ref.current!;
    const [bx0, by0, bx1, by1] = aoi.bbox;
    const pad = 1.0;
    const map = new MlMap({
      container,
      style: buildStyle(location.origin),
      center: aoi.center as [number, number],
      zoom: 8.3,
      minZoom: 6,
      maxZoom: 16,
      maxBounds: [
        [bx0 - pad, by0 - pad],
        [bx1 + pad, by1 + pad],
      ],
      attributionControl: { compact: false },
    });
    map.on("error", (e) => console.error("map:", e.error?.message ?? e));
    map.addControl(new NavigationControl({ visualizePitch: false }), "top-right");
    map.addControl(new ScaleControl({ unit: "imperial" }), "bottom-left");
    window.__aperture = { map, store, playback };

    let draw: TerraDraw | null = null;
    let prev: State | null = null;
    let lastDraw = 0;
    let lastT = NaN;
    let lastVersion = -1;
    let pending = 0;
    const unsubs: (() => void)[] = [];

    const redraw = () => {
      pending = 0;
      const s = store.get();
      drawStatic(map, s, prev);
      const moved =
        s.t !== lastT ||
        playback.version !== lastVersion ||
        prev?.flags !== s.flags ||
        prev?.selected !== s.selected ||
        prev?.trails !== s.trails;
      if (moved) {
        drawAircraft(map, playback, s, container);
        lastT = s.t;
        lastVersion = playback.version;
      }
      if (draw && prev?.drawing !== s.drawing) {
        if (s.drawing) {
          draw.clear();
          draw.setMode("polygon");
        } else {
          draw.setMode("static");
          draw.clear();
        }
      }
      prev = s;
      lastDraw = performance.now();
    };
    const schedule = () => {
      if (pending) return;
      const wait = Math.max(0, REDRAW_MS - (performance.now() - lastDraw));
      pending = window.setTimeout(() => requestAnimationFrame(redraw), wait);
    };

    map.on("load", () => {
      addOverlays(map);
      draw = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map }),
        modes: [
          new TerraDrawPolygonMode({
            styles: {
              fillColor: FENCE,
              fillOpacity: 0.15,
              outlineColor: FENCE,
              outlineWidth: 2,
              closingPointColor: FENCE,
              closingPointOutlineColor: "#ffffff",
            },
          }),
        ],
      });
      draw.start();
      draw.on("finish", (id) => {
        const f = draw!.getSnapshotFeature(id);
        store.set({ drawing: false });
        if (f && f.geometry.type === "Polygon") void runGeofence(f.geometry as Fence);
      });

      for (const layer of ["aircraft", "hit-legs"]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
      map.on("click", (e: MapMouseEvent) => {
        if (store.get().drawing) return;
        const [top] = map.queryRenderedFeatures(e.point, { layers: ["aircraft", "hit-legs"] });
        if (!top) return;
        const icao24 = top.properties.icao24 as string;
        if (top.layer.id === "hit-legs") {
          const hit = store.get().fence?.hits.find((h) => hitKey(h) === top.properties.key);
          if (hit) selectHit(hit);
        } else {
          void selectAircraft(icao24);
        }
      });

      unsubs.push(store.subscribe(schedule), playback.onData(schedule));
      redraw();
      container.dataset.mapLoaded = "true";
    });

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && store.get().drawing) store.set({ drawing: false });
    };
    window.addEventListener("keydown", onKey);

    return () => {
      window.removeEventListener("keydown", onKey);
      for (const u of unsubs) u();
      clearTimeout(pending);
      draw?.stop();
      map.remove();
      delete window.__aperture;
    };
  }, [playback]);

  return <div ref={ref} className="map" data-testid="map" />;
}
