"""Extract the AOI basemap from a Protomaps daily build and vendor fonts + sprites.

    uv run python data/snapshot/fetch_basemap.py

Networked: pmtiles range-reads build.protomaps.com; fonts/sprites come from a sparse
clone of protomaps/basemaps-assets.
"""

import shutil
import subprocess

from common import AOI, CACHE_DIR, TILES_DIR, describe_output, docker_run, update_manifest

PMTILES = "protomaps/go-pmtiles:latest"
BUILD = AOI["sources"]["protomaps_build"]
BUILD_URL = f"https://build.protomaps.com/{BUILD}.pmtiles"
MAXZOOM = 15
ASSETS_REPO = "https://github.com/protomaps/basemaps-assets"
FONTSTACKS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"]
SPRITE_FLAVORS = ["light", "dark"]


def git(*args: str, cwd=None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def fetch_assets() -> str:
    repo = CACHE_DIR / "basemaps-assets"
    if not repo.exists():
        git("clone", "--depth", "1", "--filter=blob:none", "--sparse", ASSETS_REPO, str(repo))
    paths = [f"fonts/{f}" for f in FONTSTACKS] + [f"sprites/v4/{f}*" for f in SPRITE_FLAVORS]
    git("sparse-checkout", "set", "--no-cone", *paths, cwd=repo)
    for sub in ("fonts", "sprites"):
        shutil.rmtree(TILES_DIR / sub, ignore_errors=True)
    for f in FONTSTACKS:
        shutil.copytree(repo / "fonts" / f, TILES_DIR / "fonts" / f)
    (TILES_DIR / "sprites").mkdir()
    for p in (repo / "sprites" / "v4").iterdir():
        shutil.copy2(p, TILES_DIR / "sprites" / p.name)
    return git("rev-parse", "HEAD", cwd=repo).strip()


def main() -> None:
    TILES_DIR.mkdir(parents=True, exist_ok=True)
    out = TILES_DIR / "basemap.pmtiles"
    if not out.exists():
        w, s, e, n = AOI["bbox"]
        print(f"extracting {BUILD_URL} bbox z0-{MAXZOOM}")
        docker_run(
            PMTILES,
            ["extract", BUILD_URL, "/tiles/basemap.pmtiles",
             f"--bbox={w},{s},{e},{n}", f"--maxzoom={MAXZOOM}"],
            {TILES_DIR: "/tiles"},
        )  # fmt: skip
    print("fetching fonts + sprites")
    assets_commit = fetch_assets()
    font_files = sum(1 for _ in (TILES_DIR / "fonts").rglob("*.pbf"))
    update_manifest(
        "basemap",
        {"source": BUILD_URL, "bbox": AOI["bbox"], "maxzoom": MAXZOOM,
         "outputs": {"basemap": describe_output(out)},
         "assets": {"repo": ASSETS_REPO, "commit": assets_commit, "fontstacks": FONTSTACKS,
                    "font_files": font_files, "sprites": sorted(p.name for p in (TILES_DIR / "sprites").iterdir())},
         "license": "Protomaps basemap: ODbL 1.0 (c) OpenStreetMap contributors; assets: see repo"},
    )  # fmt: skip
    print(f"  basemap {out.stat().st_size / 1e6:.0f} MB, {font_files} glyph files")


if __name__ == "__main__":
    main()
