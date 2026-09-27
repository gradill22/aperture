import { useEffect, useState } from "react";
import { api } from "../api";
import { download, findingFilename, findingMarkdown, type FindingInput } from "../export";
import { store, useStore } from "../store";
import { formatDateTime, parseIso } from "../time";
import type { GeofenceHit, Provenance } from "../types";

let provenance: Promise<Provenance | null> | null = null;
const loadProvenance = () => (provenance ??= api.provenance().catch(() => null));

/** Note + markdown export for one flagged geofence pass. */
export function FindingExport({ hit }: { hit: GeofenceHit }) {
  const [note, setNote] = useState("");
  const [prov, setProv] = useState<Provenance | null>(null);
  const tz = useStore((s) => s.tz);
  const detail = useStore((s) => (s.selectedDetail?.icao24 === hit.icao24 ? s.selectedDetail : null));

  useEffect(() => {
    void loadProvenance().then(setProv);
  }, []);

  const build = (): FindingInput | null => {
    const fence = store.get().fence;
    if (!fence) return null;
    return {
      hit,
      aircraft: detail,
      fence: fence.fence,
      fenceSource: fence.source,
      fenceAround: fence.around,
      window: [fence.start, fence.end],
      note,
      tz,
      provenance: prov,
      generatedAt: Date.now() / 1000,
    };
  };

  const onExport = () => {
    const f = build();
    if (f) download(findingFilename(f), findingMarkdown(f));
  };

  return (
    <div className="finding" data-testid="finding">
      <p className="muted">
        In fence {formatDateTime(parseIso(hit.entry), tz)} → {formatDateTime(parseIso(hit.exit), tz)}
        {hit.owner_operator ? ` · ${hit.owner_operator}` : ""}
      </p>
      <label>
        Analyst note
        <textarea
          data-testid="finding-note"
          rows={3}
          value={note}
          placeholder="Why this pass matters…"
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <button className="primary" data-testid="export-finding" onClick={onExport}>
        Export finding (.md)
      </button>
    </div>
  );
}
