import { type KeyboardEvent, type RefObject, useEffect, useId, useState } from "react";
import { openFlight, selectPlace } from "../actions";
import { api } from "../api";
import { LAYERS, useStore } from "../store";
import { LAYER_COLORS } from "../style";
import { formatClock, parseIso } from "../time";
import type { FeatureSummary, FlightResult, SearchGroup, SearchResponse } from "../types";

const MIN_CHARS = 2;
const DEBOUNCE_MS = 200;

export const SEARCH_GROUPS: { id: SearchGroup; label: string }[] = [
  { id: "flights", label: "Flights" },
  ...LAYERS.map((l) => ({ id: l, label: l[0].toUpperCase() + l.slice(1) })),
];

type Item = { group: "flights"; r: FlightResult } | { group: Exclude<SearchGroup, "flights">; r: FeatureSummary };

/** Results in chip order, flattened for keyboard navigation. */
export function flatten(res: SearchResponse | null, groups: SearchGroup[]): Item[] {
  if (!res) return [];
  return groups.flatMap((g): Item[] =>
    g === "flights"
      ? (res.groups.flights ?? []).map((r) => ({ group: g, r }))
      : (res.groups[g] ?? []).map((r) => ({ group: g, r })),
  );
}

/** OSM kind tag ("aeroway=aerodrome", "military=naval_base") as a readable word. */
export const formatKind = (kind: string): string => (kind.split("=").pop() ?? kind).replace(/_/g, " ");

function pick(item: Item): void {
  if (item.group === "flights") void openFlight(item.r.icao24);
  else void selectPlace({ id: item.r.id });
}

function FlightRow({ r }: { r: FlightResult }) {
  const tz = useStore((s) => s.tz);
  const bits = [
    r.callsigns.slice(0, 3).join(", ") + (r.callsigns.length > 3 ? "…" : ""),
    r.type_code,
    `${r.n_legs} ${r.n_legs === 1 ? "leg" : "legs"}`,
    r.first_seen && `from ${formatClock(parseIso(r.first_seen), tz, false)}`,
  ].filter(Boolean);
  return (
    <>
      <span className="result-title">
        {r.registration ?? r.icao24}
        {r.registration && <code className="muted"> {r.icao24}</code>}
        {r.military && <span className="tag mil">MIL</span>}
      </span>
      <span className="result-sub">{bits.join(" · ")}</span>
    </>
  );
}

function PlaceRow({ r }: { r: FeatureSummary }) {
  const codes = [r.icao, r.iata].filter(Boolean).join(" / ");
  return (
    <>
      <span className="result-title">
        <span className="swatch" style={{ background: LAYER_COLORS[r.layer] }} />
        {r.label}
      </span>
      <span className="result-sub">
        {formatKind(r.feature_kind)}
        {codes && ` · ${codes}`}
      </span>
    </>
  );
}

export function Search({ inputRef }: { inputRef: RefObject<HTMLInputElement | null> }) {
  const listId = useId();
  const [q, setQ] = useState("");
  const [enabled, setEnabled] = useState<SearchGroup[]>(SEARCH_GROUPS.map((g) => g.id));
  // Each answer is tagged with the request it answers, so "busy" and staleness are derived.
  const [res, setRes] = useState<{ key: string; r: SearchResponse } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const query = q.trim();
  const ready = query.length >= MIN_CHARS && enabled.length > 0;
  const key = ready ? JSON.stringify([query, enabled]) : null;

  useEffect(() => {
    if (key === null) return;
    const ctl = new AbortController();
    const timer = window.setTimeout(() => {
      api
        .search(query, enabled, ctl.signal)
        .then((r) => {
          setRes({ key, r });
          setActive(0);
        })
        .catch((e) => {
          if (!ctl.signal.aborted) setError({ key, message: String(e) });
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      ctl.abort();
    };
  }, [key, query, enabled]);

  const busy = key !== null && res?.key !== key && error?.key !== key;
  const failed = key !== null && error?.key === key ? error.message : null;
  // While the next answer loads, keep showing the previous one (filtered to the current chips).
  const items = ready ? flatten(res?.r ?? null, enabled) : [];
  const showList = open && query.length >= MIN_CHARS;

  const choose = (item: Item) => {
    pick(item);
    setOpen(false);
    inputRef.current?.blur();
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      if (items.length) setActive((a) => (a + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = items[active];
      if (item) choose(item);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (showList) setOpen(false);
      else inputRef.current?.blur();
    }
  };

  const toggle = (g: SearchGroup) =>
    setEnabled((cur) => SEARCH_GROUPS.map((x) => x.id).filter((id) => (id === g ? !cur.includes(id) : cur.includes(id))));

  return (
    <div className="search" data-testid="search">
      <input
        ref={inputRef}
        type="search"
        role="combobox"
        aria-label="Search flights and places"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList && items[active] ? `${listId}-${active}` : undefined}
        placeholder="Search flights, airports, ports, bases…  ( / )"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKey}
      />
      <div className="chips" role="group" aria-label="Search categories">
        {SEARCH_GROUPS.map((g) => (
          <button
            key={g.id}
            type="button"
            className={enabled.includes(g.id) ? "chip on" : "chip"}
            aria-pressed={enabled.includes(g.id)}
            onMouseDown={(e) => e.preventDefault()} // keep the input focused and the results open
            onClick={() => toggle(g.id)}
          >
            {g.id !== "flights" && <span className="swatch" style={{ background: LAYER_COLORS[g.id] }} />}
            {g.label}
          </button>
        ))}
      </div>
      {showList && (
        <div className="results" id={listId} role="listbox" aria-label="Search results" data-testid="search-results">
          {enabled.length === 0 && <p className="hint">Select at least one category.</p>}
          {failed && <p className="error">{failed}</p>}
          {ready && !failed && !busy && items.length === 0 && <p className="hint">No matches.</p>}
          {ready && busy && items.length === 0 && <p className="hint">Searching…</p>}
          {items.map((item, i) => {
            const first = i === 0 || items[i - 1].group !== item.group;
            const header = first ? SEARCH_GROUPS.find((g) => g.id === item.group)!.label : null;
            return (
              <div key={`${item.group}:${item.r.id}`} role="presentation">
                {header && (
                  <div className="result-group" role="presentation">
                    {header}
                  </div>
                )}
                <div
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={i === active}
                  className={i === active ? "result active" : "result"}
                  onMouseDown={(e) => e.preventDefault()} // keep focus until the click lands
                  onMouseEnter={() => setActive(i)}
                  onClick={() => choose(item)}
                >
                  {item.group === "flights" ? <FlightRow r={item.r} /> : <PlaceRow r={item.r} />}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
