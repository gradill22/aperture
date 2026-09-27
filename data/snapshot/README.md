# Data snapshot

The only networked code in the repo (with `bootstrap/`). It runs once; outputs are gitignored and
pinned by `MANIFEST.json` (sources, sha256, row/feature counts). Everything downstream is offline.

AOI and date come from `data/aoi.json`: bbox enclosing 50 mi of DC, 2026-09-24.

```bash
# raw downloads (~4.9 GB) go to the cache dir, outside the repo
export APERTURE_CACHE_DIR=D:/aperture-cache
export MSYS_NO_PATHCONV=1                            # Git Bash on Windows: keep docker paths intact

uv run python data/snapshot/fetch_adsb.py download   # adsb.lol globe_history day (networked)
uv run python data/snapshot/fetch_adsb.py filter     # full-day traces of aircraft entering the bbox
uv run python data/snapshot/fetch_osm.py             # Geofabrik DC/MD/VA/WV -> 4 overlay layers
uv run python data/snapshot/fetch_basemap.py         # Protomaps bbox extract + fonts/sprites
uv run python data/tiles/bake.py                     # overlays -> infrastructure.pmtiles (offline)
uv run python data/snapshot/make_fixtures.py         # committed test subset (offline)
uv run python scripts/verify.py phase0               # gate: manifest + network-import lint
```

| Output | Source | License |
|---|---|---|
| `adsb/2026-09-24/{positions,aircraft}.parquet` | [adsb.lol](https://github.com/adsblol/globe_history_2026) `v2026.09.24-planes-readsb-prod-0` | ODbL 1.0 |
| `osm/{airports,ports,government,military}.geojsonl` | [Geofabrik](https://download.geofabrik.de/) extracts | ODbL 1.0, © OpenStreetMap contributors |
| `../tiles/basemap.pmtiles`, `fonts/`, `sprites/` | [Protomaps](https://build.protomaps.com/) build 20260924, [basemaps-assets](https://github.com/protomaps/basemaps-assets) | ODbL 1.0 (basemap); see repo (assets) |

Ports keep only named piers (unnamed `man_made=pier` are mostly private docks).
