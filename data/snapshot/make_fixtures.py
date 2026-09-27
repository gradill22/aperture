"""Write a small, deterministic, committed subset of the snapshot to data/fixtures/ (offline).

    uv run python data/snapshot/make_fixtures.py

Used by unit/integration tests and CI, which never see the full snapshot.
"""

import json
import math

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from common import AOI, DATA_DIR, SNAPSHOT_DIR

ADSB_DIR = SNAPSHOT_DIR / "adsb" / AOI["date"]
OSM_DIR = SNAPSHOT_DIR / "osm"
FIXTURES = DATA_DIR / "fixtures"
DCA_ICAO = "KDCA"
LANDMARK_ICAO = {"KDCA", "KIAD", "KBWI", "KADW"}  # airports whose features are always included
N_NEAR_DCA, N_HELI, N_MIL, N_OTHER = 10, 5, 5, 5


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def read_features(layer: str) -> list[dict]:
    with (OSM_DIR / f"{layer}.geojsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def centroid(geom: dict) -> tuple[float, float]:
    """Mean of all coordinates; good enough to locate an airport."""
    pts: list = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            pts.append(c)
        else:
            for x in c:
                walk(x)

    walk(geom["coordinates"])
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def pick_osm() -> tuple[dict[str, list[dict]], tuple[float, float]]:
    picked: dict[str, list[dict]] = {}
    dca = None
    for layer in ("airports", "ports", "government", "military"):
        feats = sorted(
            read_features(layer), key=lambda f: (f["properties"]["@type"], f["properties"]["@id"])
        )
        landmarks = [f for f in feats if f["properties"].get("icao") in LANDMARK_ICAO]
        named = [f for f in feats if "name" in f["properties"] and f not in landmarks]
        picked[layer] = landmarks + named[:12]
        for f in landmarks:
            if (
                f["properties"].get("icao") == DCA_ICAO
                and f["properties"].get("aeroway") == "aerodrome"
            ):
                dca = centroid(f["geometry"])
    if dca is None:
        raise SystemExit("KDCA aerodrome not found in airports layer")
    return picked, dca


def pick_aircraft(dca: tuple[float, float]) -> list[str]:
    ac = pq.read_table(ADSB_DIR / "aircraft.parquet").to_pylist()
    pos = pq.read_table(ADSB_DIR / "positions.parquet", columns=["icao24", "lon", "lat"])
    near = pc.less(
        pc.add(
            pc.power(pc.subtract(pos["lon"], dca[0]), 2),
            pc.power(pc.subtract(pos["lat"], dca[1]), 2),
        ),
        0.03**2,  # coarse ~3 km prefilter, refined below
    )
    candidates = set(pos.filter(near)["icao24"].to_pylist())
    near_dca = sorted(
        i for i in candidates
        if any(haversine_km(r["lon"], r["lat"], *dca) <= 2.0
               for r in pos.filter(pc.equal(pos["icao24"], i)).to_pylist())
    )[:N_NEAR_DCA]  # fmt: skip
    chosen = list(near_dca)
    by_icao = sorted(ac, key=lambda a: a["icao24"])

    def take(pred, n):
        for a in [a for a in by_icao if pred(a) and a["icao24"] not in chosen][:n]:
            chosen.append(a["icao24"])

    take(lambda a: a["category"] == "A7", N_HELI)
    take(lambda a: a["military"], N_MIL)
    take(lambda a: a["n_points_in_aoi"] > 100, N_OTHER)
    return chosen


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    osm, dca = pick_osm()
    for layer, feats in osm.items():
        text = "".join(json.dumps(f, separators=(",", ":")) + "\n" for f in feats)
        (FIXTURES / f"{layer}.geojsonl").write_text(text, encoding="utf-8", newline="\n")
    icaos = pick_aircraft(dca)
    wanted = pa.array(icaos)
    ac = pq.read_table(ADSB_DIR / "aircraft.parquet")
    pq.write_table(
        ac.filter(pc.is_in(ac["icao24"], value_set=wanted)), FIXTURES / "aircraft.parquet"
    )
    pos = pq.read_table(ADSB_DIR / "positions.parquet")
    pos = pos.filter(pc.is_in(pos["icao24"], value_set=wanted))
    pq.write_table(pos, FIXTURES / "positions.parquet", compression="zstd")
    summary = {
        "source": "subset of data/snapshot (see MANIFEST.json)",
        "dca_centroid": [round(dca[0], 6), round(dca[1], 6)],
        "aircraft": icaos,
        "positions": pos.num_rows,
        "features": {k: len(v) for k, v in osm.items()},
    }
    (FIXTURES / "fixtures.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "aircraft"}, indent=2))


if __name__ == "__main__":
    main()
