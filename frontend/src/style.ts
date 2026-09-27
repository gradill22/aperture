// Map style. Every URL is on the page's own origin: the edge serves fonts and sprites and
// proxies /tiles to martin. Nothing here may point anywhere else (enforced by eslint + e2e).
import { layers as basemapLayers, namedFlavor } from "@protomaps/basemaps";
import type { LayerSpecification, StyleSpecification } from "maplibre-gl";
import type { Layer } from "./types";

export const LAYER_COLORS: Record<Layer, string> = {
  airports: "#7c3aed",
  ports: "#0e7490",
  government: "#b45309",
  military: "#dc2626",
};

export const ATTRIBUTION = [
  "© OpenStreetMap contributors (ODbL)",
  "Basemap © Protomaps",
  "ADS-B © adsb.lol (ODbL 1.0)",
].join(" | ");

function infrastructureLayers(layer: Layer): LayerSpecification[] {
  const color = LAYER_COLORS[layer];
  const common = { source: "infrastructure", "source-layer": layer } as const;
  return [
    {
      ...common,
      id: `infra-${layer}-fill`,
      type: "fill",
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": color, "fill-opacity": 0.12 },
    },
    {
      ...common,
      id: `infra-${layer}-outline`,
      type: "line",
      filter: ["in", ["geometry-type"], ["literal", ["Polygon", "LineString"]]],
      paint: { "line-color": color, "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 14, 1.5], "line-opacity": 0.7 },
    },
    {
      ...common,
      id: `infra-${layer}-point`,
      type: "circle",
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-color": color,
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 1.5, 10, 2.5, 14, 5],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1,
      },
    },
    {
      ...common,
      id: `infra-${layer}-label`,
      type: "symbol",
      minzoom: layer === "airports" || layer === "military" ? 9 : 12,
      filter: ["has", "name"],
      layout: {
        "text-field": ["get", "name"],
        "text-font": ["Noto Sans Medium"],
        "text-size": 11,
        "text-max-width": 10,
        "symbol-placement": "point",
      },
      paint: { "text-color": color, "text-halo-color": "#ffffff", "text-halo-width": 1.5 },
    },
  ];
}

export const INFRA_LAYERS: Layer[] = ["airports", "ports", "government", "military"];

export function infraLayerIds(layer: Layer): string[] {
  return ["fill", "outline", "point", "label"].map((k) => `infra-${layer}-${k}`);
}

export function buildStyle(origin: string): StyleSpecification {
  return {
    version: 8,
    glyphs: `${origin}/fonts/{fontstack}/{range}.pbf`,
    sprite: `${origin}/sprites/light`,
    sources: {
      basemap: {
        type: "vector",
        tiles: [`${origin}/tiles/basemap/{z}/{x}/{y}`],
        minzoom: 0,
        maxzoom: 15,
        attribution: ATTRIBUTION,
      },
      infrastructure: {
        type: "vector",
        tiles: [`${origin}/tiles/infrastructure/{z}/{x}/{y}`],
        minzoom: 4,
        maxzoom: 14,
      },
    },
    layers: [
      ...(basemapLayers("basemap", namedFlavor("light"), { lang: "en" }) as LayerSpecification[]),
      ...INFRA_LAYERS.flatMap(infrastructureLayers),
    ],
  };
}
