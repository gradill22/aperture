"""Fetch one day of adsb.lol globe_history and keep full traces of aircraft that entered the AOI.

uv run python data/snapshot/fetch_adsb.py download   # networked, resumable (~4 GB)
uv run python data/snapshot/fetch_adsb.py filter     # offline: tar -> parquet
"""

import gzip
import io
import sys
import tarfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import httpx
import orjson
import pyarrow as pa
import pyarrow.parquet as pq
from common import (
    AOI,
    CACHE_DIR,
    SNAPSHOT_DIR,
    describe_output,
    download,
    in_bbox,
    sha256_file,
    update_manifest,
)

RELEASE = AOI["sources"]["adsb_release"]
REPO = AOI["sources"]["adsb_repo"]
RAW_DIR = CACHE_DIR / "adsb" / RELEASE
OUT_DIR = SNAPSHOT_DIR / "adsb" / AOI["date"]

# readsb trace point: [dt, lat, lon, alt_baro|'ground'|None, gs, track, flags, vrate,
#                      details|None, source_type, geom_alt, geom_rate, ias, roll]
FLAG_NEW_LEG = 2
DB_FLAGS = {1: "military", 2: "interesting", 4: "pia", 8: "ladd"}

POSITION_SCHEMA = pa.schema([
    ("icao24", pa.string()),
    ("ts", pa.timestamp("ms", tz="UTC")),
    ("lon", pa.float64()),
    ("lat", pa.float64()),
    ("alt_baro_ft", pa.int32()),
    ("on_ground", pa.bool_()),
    ("gs_kt", pa.float32()),
    ("track_deg", pa.float32()),
    ("vrate_fpm", pa.int32()),
    ("geom_alt_ft", pa.int32()),
    ("callsign", pa.string()),
    ("squawk", pa.string()),
    ("new_leg", pa.bool_()),
    ("source", pa.string()),
])  # fmt: skip


class _Chain(io.RawIOBase):
    """Read the split .tar.aa/.ab/... parts as one stream without joining them on disk."""

    def __init__(self, paths: list[Path]):
        self.files = [p.open("rb") for p in paths]
        self.i = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        while self.i < len(self.files):
            if n := self.files[self.i].readinto(b):
                return n
            self.i += 1
        return 0


def iter_traces(parts: list[Path]) -> Iterator[dict]:
    stream = io.BufferedReader(_Chain(parts), 1 << 22)
    with tarfile.open(fileobj=stream, mode="r|") as tf:
        for member in tf:
            if member.isfile() and "/trace_full_" in member.name:
                raw = tf.extractfile(member).read()
                yield orjson.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def _int(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def trace_rows(trace: dict) -> tuple[dict, dict]:
    """Decode one aircraft-day into column lists, carrying callsign/squawk forward."""
    icao, base = trace["icao"], trace["timestamp"]
    cols: dict[str, list] = {f.name: [] for f in POSITION_SCHEMA}
    callsign = squawk = None
    categories: Counter = Counter()
    for p in trace["trace"]:
        details = p[8] if len(p) > 8 else None
        if details:
            callsign = (details.get("flight") or "").strip() or callsign
            squawk = details.get("squawk", squawk)
            if cat := details.get("category"):
                categories[cat] += 1
        alt = p[3]
        cols["icao24"].append(icao)
        cols["ts"].append(int((base + p[0]) * 1000))
        cols["lon"].append(p[2])
        cols["lat"].append(p[1])
        cols["alt_baro_ft"].append(_int(alt))
        cols["on_ground"].append(alt == "ground")
        cols["gs_kt"].append(p[4])
        cols["track_deg"].append(p[5])
        cols["vrate_fpm"].append(_int(p[7]))
        cols["geom_alt_ft"].append(_int(p[10]) if len(p) > 10 else None)
        cols["callsign"].append(callsign)
        cols["squawk"].append(squawk)
        cols["new_leg"].append(bool((p[6] or 0) & FLAG_NEW_LEG))
        cols["source"].append(p[9] if len(p) > 9 else None)
    flags = trace.get("dbFlags") or 0
    aircraft = {
        "icao24": icao,
        "registration": trace.get("r"),
        "type_code": trace.get("t"),
        "description": trace.get("desc"),
        "owner_operator": trace.get("ownOp"),
        "year": trace.get("year"),
        "category": categories.most_common(1)[0][0] if categories else None,
        "db_flags": flags,
        **{name: bool(flags & bit) for bit, name in DB_FLAGS.items()},
        "n_points": len(trace["trace"]),
        "n_points_in_aoi": sum(in_bbox(p[2], p[1]) for p in trace["trace"]),
    }
    return cols, aircraft


def cmd_filter() -> None:
    assets = release_assets_cached()
    parts = [RAW_DIR / a["name"] for a in assets]
    for a, p in zip(assets, parts, strict=True):
        if not p.exists() or p.stat().st_size != a["size"]:
            sys.exit(f"{p.name} missing or incomplete; run `download` first")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pos_path = OUT_DIR / "positions.parquet"
    aircraft_rows: list[dict] = []
    scanned = 0
    with pq.ParquetWriter(pos_path, POSITION_SCHEMA, compression="zstd") as writer:
        for trace in iter_traces(parts):
            scanned += 1
            if scanned % 20000 == 0:
                print(f"  scanned {scanned} aircraft, kept {len(aircraft_rows)}", flush=True)
            if not any(in_bbox(p[2], p[1]) for p in trace["trace"]):
                continue
            cols, aircraft = trace_rows(trace)
            writer.write_table(pa.table(cols, schema=POSITION_SCHEMA))
            aircraft_rows.append(aircraft)
    ac_path = OUT_DIR / "aircraft.parquet"
    pq.write_table(pa.Table.from_pylist(aircraft_rows), ac_path, compression="zstd")

    n_points = pq.ParquetFile(pos_path).metadata.num_rows
    print(f"  kept {len(aircraft_rows)} of {scanned} aircraft, {n_points} points")
    update_manifest(
        "adsb",
        {"release": RELEASE, "repo": REPO, "date": AOI["date"], "bbox": AOI["bbox"],
         "selection": "full-day trace of every aircraft with >=1 point inside bbox",
         "sources": [a | {"sha256": sha256_file(p)} for a, p in zip(assets, parts, strict=True)],
         "aircraft_scanned": scanned,
         "outputs": {"positions": describe_output(pos_path) | {"rows": n_points},
                     "aircraft": describe_output(ac_path) | {"rows": len(aircraft_rows)}},
         "license": "ODbL 1.0, adsb.lol"},
    )  # fmt: skip


def release_assets_cached() -> list[dict]:
    """Asset list is cached so `filter` stays offline after `download`."""
    cache = RAW_DIR / "assets.json"
    if not cache.exists():
        cache.write_bytes(orjson.dumps(release_assets()))
    return orjson.loads(cache.read_bytes())


def release_assets() -> list[dict]:
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE}"
    r = httpx.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    assets = sorted(r.json()["assets"], key=lambda a: a["name"])
    return [
        {"name": a["name"], "size": a["size"], "url": a["browser_download_url"]} for a in assets
    ]


def cmd_download() -> None:

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for a in release_assets_cached():
        download(a["url"], RAW_DIR / a["name"], expected_size=a["size"])


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    commands = {"download": cmd_download, "filter": cmd_filter}
    if cmd not in commands:
        sys.exit(__doc__)
    commands[cmd]()


if __name__ == "__main__":
    main()
