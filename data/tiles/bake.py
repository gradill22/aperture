"""Bake the OSM infrastructure overlays into data/tiles/infrastructure.pmtiles (offline).

    uv run python data/tiles/bake.py

One source-layer per category. The dataset is small (~2.5k features), so nothing is
dropped at low zoom: every airport/port/base stays visible when zoomed out.
"""

import subprocess
import sys
from pathlib import Path

TILES_DIR = Path(__file__).resolve().parent
OSM_DIR = TILES_DIR.parent / "snapshot" / "osm"
sys.path.insert(0, str(TILES_DIR.parent / "snapshot"))
from common import describe_output, update_manifest

TIPPECANOE = "indigoag/tippecanoe:latest"
LAYERS = ["airports", "ports", "government", "military"]


def main() -> None:
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{OSM_DIR.as_posix()}:/osm:ro",
        "-v", f"{TILES_DIR.as_posix()}:/tiles",
        TIPPECANOE, "tippecanoe",
        "-o", "/tiles/infrastructure.pmtiles", "--force", "--quiet",
        "-n", "Aperture infrastructure", "-A", "(c) OpenStreetMap contributors",
        "-Z4", "-z14", "-r1", "--no-feature-limit", "--no-tile-size-limit",
        *[arg for layer in LAYERS for arg in ("-L", f"{layer}:/osm/{layer}.geojsonl")],
    ]  # fmt: skip
    subprocess.run(cmd, check=True)
    out = TILES_DIR / "infrastructure.pmtiles"
    update_manifest(
        "infrastructure_tiles",
        {"tool": f"{TIPPECANOE} (tippecanoe v2.23.0)", "layers": LAYERS, "zoom": [4, 14],
         "outputs": {"infrastructure": describe_output(out)}},
    )  # fmt: skip
    print(f"  {out.name}: {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
