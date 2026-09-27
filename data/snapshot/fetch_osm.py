"""Fetch Geofabrik extracts and cut the four infrastructure overlay layers to the AOI bbox.

    uv run python data/snapshot/fetch_osm.py

Downloads are networked (cached in APERTURE_CACHE_DIR); osmium runs offline in the
bootstrap image aperture/osmium:1.
"""

import json

from common import (
    AOI,
    CACHE_DIR,
    SNAPSHOT_DIR,
    describe_output,
    docker_run,
    download,
    update_manifest,
)

OSMIUM = "aperture/osmium:1"
GEOFABRIK = "https://download.geofabrik.de/north-america/us/{region}-latest.osm.pbf"
RAW_DIR = CACHE_DIR / "osm"
OUT_DIR = SNAPSHOT_DIR / "osm"

LAYERS = {
    "airports": ["nwr/aeroway=aerodrome,heliport,helipad,runway,terminal"],
    "ports": [
        "nwr/landuse=port",
        "nwr/industrial=port",
        "nwr/harbour",
        "nwr/leisure=marina",
        "nwr/amenity=ferry_terminal",
        "nwr/man_made=pier",
    ],
    "government": ["nwr/office=government", "nwr/building=government", "nwr/government"],
    "military": ["nwr/landuse=military", "nwr/military"],
}

# Closed ways become polygons only (osmium's default also emits a duplicate LineString, and so
# would any linear_tags match), giving one feature per OSM object; area=no keeps them linear.
EXPORT_CONFIG = {"linear_tags": ["area=no"], "area_tags": True}


PORT_TAGS = {
    "landuse": "port",
    "industrial": "port",
    "leisure": "marina",
    "amenity": "ferry_terminal",
}


def keep(layer: str, props: dict) -> bool:
    # ~95% of piers in the AOI are unnamed private docks; keep a pier only if it is named
    # or also matches one of the other port filters.
    if layer != "ports" or props.get("man_made") != "pier":
        return True
    other_port_tag = "harbour" in props or any(props.get(k) == v for k, v in PORT_TAGS.items())
    return "name" in props or other_port_tag


def osmium(*args: str) -> str:
    return docker_run(OSMIUM, list(args), {RAW_DIR: "/raw", OUT_DIR: "/out"})


def main() -> None:
    regions = AOI["sources"]["geofabrik_regions"]
    sources = []
    for region in regions:
        url = GEOFABRIK.format(region=region)
        path = download(url, RAW_DIR / f"{region}-latest.osm.pbf")
        ts = osmium(
            "fileinfo", "-g", "header.option.osmosis_replication_timestamp", f"/raw/{path.name}"
        )
        sources.append(
            {"url": url, "replication_timestamp": ts.strip(), "bytes": path.stat().st_size}
        )
        print(f"  {region}: replication {ts.strip()}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    w, s, e, n = AOI["bbox"]
    print("merging + clipping to bbox")
    (RAW_DIR / "export-config.json").write_text(json.dumps(EXPORT_CONFIG))
    osmium(
        "merge",
        *[f"/raw/{r}-latest.osm.pbf" for r in regions],
        "-o",
        "/raw/merged.osm.pbf",
        "--overwrite",
    )
    osmium(
        "extract", "-b", f"{w},{s},{e},{n}", "-s", "smart",
        "/raw/merged.osm.pbf", "-o", "/raw/aoi.osm.pbf", "--overwrite",
    )  # fmt: skip

    outputs = {}
    for layer, filters in LAYERS.items():
        # -t strips tags from objects pulled in only to complete geometry (e.g. gate nodes)
        osmium(
            "tags-filter", "-t", "/raw/aoi.osm.pbf", *filters,
            "-o", f"/raw/{layer}.osm.pbf", "--overwrite",
        )  # fmt: skip
        osmium(
            "export", f"/raw/{layer}.osm.pbf", "-f", "geojsonseq",
            "-c", "/raw/export-config.json", "-x", "print_record_separator=false",
            "-a", "type,id", "-o", f"/out/{layer}.geojsonl", "--overwrite",
        )  # fmt: skip
        out = OUT_DIR / f"{layer}.geojsonl"
        lines = out.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if keep(layer, json.loads(ln)["properties"])]
        out.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
        count = len(kept)
        outputs[layer] = describe_output(out) | {"features": count}
        print(f"  {layer}: {count} features")

    update_manifest(
        "osm",
        {"bbox": AOI["bbox"], "filters": LAYERS, "export_config": EXPORT_CONFIG,
         "post_filter": "ports: unnamed man_made=pier dropped", "sources": sources, "outputs": outputs,
         "license": "ODbL 1.0, (c) OpenStreetMap contributors"},
    )  # fmt: skip


if __name__ == "__main__":
    main()
