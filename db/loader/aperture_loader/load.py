"""Load the snapshot (or the test fixtures) into Postgres. Idempotent: truncates first.

    aperture-load --adsb-dir DIR --osm-dir DIR [--force]   (defaults: $ADSB_DIR, $OSM_DIR)

ADSB_DIR holds positions.parquet + aircraft.parquet; OSM_DIR holds <layer>.geojsonl.
Skips the load when the database already holds these exact files (sha256 fingerprint)
unless --force is given.
"""

import argparse
import hashlib
import io
import json
import os
import time
from pathlib import Path

import psycopg
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

LAYERS = ("airports", "ports", "government", "military")

# (key, accepted values or None for any) in priority order; mirrors the snapshot tag filters.
KIND_RULES: dict[str, list[tuple[str, set[str] | None]]] = {
    "airports": [("aeroway", {"aerodrome", "heliport", "helipad", "runway", "terminal"})],
    "ports": [
        ("landuse", {"port"}),
        ("industrial", {"port"}),
        ("harbour", None),
        ("leisure", {"marina"}),
        ("amenity", {"ferry_terminal"}),
        ("man_made", {"pier"}),
    ],
    "government": [("government", None), ("office", {"government"}), ("building", {"government"})],
    "military": [("military", None), ("landuse", {"military"})],
}
OSM_TYPES = {"n": "node", "w": "way", "r": "relation"}

AIRCRAFT_COLUMNS = [
    "icao24", "registration", "type_code", "description", "owner_operator", "year", "category",
    "db_flags", "military", "interesting", "pia", "ladd", "n_points", "n_points_in_aoi",
]  # fmt: skip
POSITION_COLUMNS = [
    "ts", "icao24", "lon", "lat", "alt_baro_ft", "on_ground", "gs_kt", "track_deg",
    "vrate_fpm", "geom_alt_ft", "callsign", "squawk", "new_leg", "source",
]  # fmt: skip


def feature_kind(layer: str, tags: dict) -> str:
    for key, values in KIND_RULES[layer]:
        if key in tags and (values is None or tags[key] in values):
            return f"{key}={tags[key]}"
    return "other"


def connect(url: str, timeout_s: int = 120) -> psycopg.Connection:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return psycopg.connect(url)
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def copy_csv(cur: psycopg.Cursor, table: str, columns: list[str], batches) -> int:
    """Stream Arrow record batches into COPY ... FORMAT csv (empty unquoted field = NULL)."""
    rows = 0
    opts = pacsv.WriteOptions(include_header=False)
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN (FORMAT csv)") as copy:
        for batch in batches:
            buf = io.BytesIO()
            pacsv.write_csv(batch.select(columns), buf, write_options=opts)
            copy.write(buf.getvalue())
            rows += batch.num_rows
    return rows


def load_osm(cur: psycopg.Cursor, osm_dir: Path) -> int:
    cur.execute(
        "CREATE TEMP TABLE osm_stage (layer text, osm_type text, osm_id bigint, name text,"
        " kind text, tags jsonb, geojson text) ON COMMIT DROP"
    )
    n = 0
    with cur.copy("COPY osm_stage FROM STDIN") as copy:
        for layer in LAYERS:
            with (osm_dir / f"{layer}.geojsonl").open(encoding="utf-8") as f:
                for line in f:
                    feat = json.loads(line)
                    props = feat["properties"]
                    tags = {k: v for k, v in props.items() if not k.startswith("@")}
                    copy.write_row((
                        layer, OSM_TYPES[props["@type"][0]], props["@id"], tags.get("name"),
                        feature_kind(layer, tags), json.dumps(tags), json.dumps(feat["geometry"]),
                    ))  # fmt: skip
                    n += 1
    cur.execute(
        "INSERT INTO osm_feature (layer, osm_type, osm_id, name, kind, tags, geom)"
        " SELECT layer, osm_type, osm_id, name, kind, tags,"
        "        ST_SetSRID(ST_GeomFromGeoJSON(geojson), 4326) FROM osm_stage"
    )
    return n


def load_adsb(cur: psycopg.Cursor, adsb_dir: Path) -> tuple[int, int]:
    aircraft = pq.read_table(adsb_dir / "aircraft.parquet")
    n_aircraft = copy_csv(cur, "aircraft", AIRCRAFT_COLUMNS, aircraft.to_batches())

    cur.execute(
        "CREATE TEMP TABLE pos_stage (ts timestamptz, icao24 text, lon float8, lat float8,"
        " alt_baro_ft int, on_ground boolean, gs_kt real, track_deg real, vrate_fpm int,"
        " geom_alt_ft int, callsign text, squawk text, new_leg boolean, source text)"
        " ON COMMIT DROP"
    )
    positions = pq.ParquetFile(adsb_dir / "positions.parquet")
    n_positions = copy_csv(cur, "pos_stage", POSITION_COLUMNS, positions.iter_batches(200_000))
    cur.execute(
        "INSERT INTO adsb_position (ts, icao24, geom, alt_baro_ft, on_ground, gs_kt, track_deg,"
        "  vrate_fpm, geom_alt_ft, callsign, squawk, new_leg, source)"
        " SELECT ts, icao24, ST_Point(lon, lat, 4326), alt_baro_ft, on_ground, gs_kt, track_deg,"
        "  vrate_fpm, geom_alt_ft, callsign, squawk, new_leg, source FROM pos_stage ORDER BY ts"
    )
    cur.execute(
        "UPDATE aircraft a SET callsigns = s.cs FROM ("
        "  SELECT icao24, array_agg(DISTINCT callsign ORDER BY callsign) AS cs"
        "  FROM adsb_position WHERE callsign IS NOT NULL GROUP BY icao24) s"
        " WHERE a.icao24 = s.icao24"
    )
    return n_aircraft, n_positions


def fingerprint(adsb_dir: Path, osm_dir: Path) -> str:
    h = hashlib.sha256()
    files = [adsb_dir / "aircraft.parquet", adsb_dir / "positions.parquet"]
    files += [osm_dir / f"{layer}.geojsonl" for layer in LAYERS]
    for path in files:
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adsb-dir", type=Path, default=os.environ.get("ADSB_DIR"))
    parser.add_argument("--osm-dir", type=Path, default=os.environ.get("OSM_DIR"))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--force", action="store_true", help="reload even if already loaded")
    args = parser.parse_args()
    if not (args.adsb_dir and args.osm_dir and args.database_url):
        parser.error("adsb dir, osm dir and database url are required")

    started = time.monotonic()
    digest = fingerprint(args.adsb_dir, args.osm_dir)
    with connect(args.database_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT fingerprint FROM load_info ORDER BY loaded_at DESC LIMIT 1")
        row = cur.fetchone()
        if row and row[0] == digest and not args.force:
            print(f"already loaded ({digest[:12]}); skipping", flush=True)
            return
        cur.execute("TRUNCATE osm_feature, aircraft, adsb_position, load_info RESTART IDENTITY")
        n_features = load_osm(cur, args.osm_dir)
        print(f"osm_feature: {n_features}", flush=True)
        n_aircraft, n_positions = load_adsb(cur, args.adsb_dir)
        print(f"aircraft: {n_aircraft}, adsb_position: {n_positions}", flush=True)
        cur.execute("REFRESH MATERIALIZED VIEW flight")
        cur.execute("SELECT count(*) FROM flight")
        (n_flights,) = cur.fetchone() or (0,)
        counts = {"osm_feature": n_features, "aircraft": n_aircraft,
                  "adsb_position": n_positions, "flight": n_flights}  # fmt: skip
        cur.execute(
            "INSERT INTO load_info (source, fingerprint, counts) VALUES (%s, %s, %s)",
            (f"{args.adsb_dir} + {args.osm_dir}", digest, json.dumps(counts)),
        )
    with connect(args.database_url) as conn:
        conn.autocommit = True
        conn.execute("ANALYZE")
    print(f"flight: {n_flights}; loaded in {time.monotonic() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
