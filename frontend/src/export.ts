// Markdown finding for a flagged geofence pass. Generated entirely in the browser.
import { formatDateTime, formatDuration, parseIso } from "./time";
import type { FenceAround } from "./store";
import type { AircraftDetail, Fence, GeofenceHit, Provenance, Tz } from "./types";

export interface FindingInput {
  hit: GeofenceHit;
  aircraft: AircraftDetail | null;
  fence: Fence;
  fenceSource: "drawn" | "chat" | "place";
  fenceAround?: FenceAround;
  window: [number, number];
  note: string;
  tz: Tz;
  provenance: Provenance | null;
  generatedAt: number;
}

const flagList = (h: Pick<GeofenceHit, "military" | "ladd" | "pia">, interesting?: boolean) =>
  [h.military && "military", h.ladd && "LADD", h.pia && "PIA", interesting && "interesting"].filter(Boolean).join(", ") ||
  "none";

const orDash = (v: string | number | null | undefined) => (v === null || v === undefined || v === "" ? "—" : String(v));

/** Both zones when the display zone is ET, so the finding is unambiguous. */
function when(iso: string, tz: Tz): string {
  const t = parseIso(iso);
  return tz === "UTC" ? formatDateTime(t, "UTC") : `${formatDateTime(t, "ET")} (${formatDateTime(t, "UTC")})`;
}

export const formatKm = (m: number): string =>
  `${(m / 1000).toLocaleString("en-US", { maximumFractionDigits: 2 })} km`;

function fenceSourceText(f: FindingInput): string {
  if (f.fenceSource === "place" && f.fenceAround) {
    const a = f.fenceAround;
    return `${formatKm(a.buffer_m)} buffer around ${a.name} (OSM ${a.osm})`;
  }
  return f.fenceSource === "drawn" ? "drawn on the map" : "created by the chat assistant";
}

export function findingFilename(f: FindingInput): string {
  const stamp = new Date(parseIso(f.hit.entry) * 1000).toISOString().slice(0, 16).replace(/[-:]/g, "").replace("T", "-");
  return `finding-${f.hit.icao24}-${stamp}Z.md`;
}

export function findingMarkdown(f: FindingInput): string {
  const { hit, aircraft: ac, tz } = f;
  const label = hit.registration ?? hit.callsign ?? hit.icao24;
  const lines: string[] = [
    `# Finding: ${label} (${hit.icao24}) crossed geofence`,
    "",
    `- **Entry:** ${when(hit.entry, tz)}`,
    `- **Exit:** ${when(hit.exit, tz)}`,
    `- **Time inside:** ${formatDuration(hit.duration_s)} (${hit.n_points_inside} position reports)`,
    `- **Altitude inside:** ${orDash(hit.min_alt_ft)}–${orDash(hit.max_alt_ft)} ft baro${hit.on_ground ? " (reported on ground inside the fence)" : ""}`,
    "",
    "## Aircraft",
    "",
    "| Field | Value |",
    "| --- | --- |",
    `| ICAO 24-bit address | \`${hit.icao24}\` |`,
    `| Registration | ${orDash(hit.registration)} |`,
    `| Callsign (this leg) | ${orDash(hit.callsign)} |`,
    `| Type | ${orDash(hit.type_code)}${hit.description ? ` — ${hit.description}` : ""} |`,
    `| Owner / operator | ${orDash(hit.owner_operator)} |`,
    `| Registry flags | ${flagList(hit, ac?.interesting)} |`,
  ];
  if (ac) {
    lines.push(`| Callsigns seen (day) | ${ac.callsigns.length ? ac.callsigns.join(", ") : "—"} |`);
    lines.push(`| Legs flown (day) | ${ac.legs.length} |`);
  }
  lines.push("", "## Analyst note", "", f.note.trim() || "_No note._", "");
  lines.push(
    "## Geofence",
    "",
    `- **Source:** ${fenceSourceText(f)}`,
    `- **Search window:** ${when(new Date(f.window[0] * 1000).toISOString(), tz)} → ${when(new Date(f.window[1] * 1000).toISOString(), tz)}`,
    `- **Pass:** leg ${hit.leg} of this aircraft's day`,
    "",
    "```geojson",
    JSON.stringify(f.fence),
    "```",
    "",
  );
  if (hit.leg_geometry) {
    lines.push(
      "<details><summary>Leg geometry (simplified to ~20 m)</summary>",
      "",
      "```geojson",
      JSON.stringify(hit.leg_geometry),
      "```",
      "",
      "</details>",
      "",
    );
  }
  lines.push("## Provenance", "");
  const p = f.provenance;
  if (p) {
    lines.push(
      `- **ADS-B:** ${p.adsb.repo} release \`${p.adsb.release}\` (${p.adsb.date}); ${p.adsb.selection}. License: ${p.adsb.license}.`,
      `- **OSM infrastructure:** ${p.osm.sources.map((s) => `${s.url.split("/").pop()} @ ${s.replication_timestamp}`).join("; ")}. License: ${p.osm.license}.`,
      `- **Basemap:** ${p.basemap.source}. License: ${p.basemap.license}.`,
    );
  } else {
    lines.push("- Snapshot manifest unavailable.");
  }
  lines.push(
    `- **Generated:** ${formatDateTime(f.generatedAt, "UTC")} by Aperture (offline).`,
    "- Positions are as broadcast by the aircraft (ADS-B/MLAT) and received by the adsb.lol network; gaps are coverage gaps, not proof of absence.",
    "",
  );
  return lines.join("\n");
}

export function download(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
