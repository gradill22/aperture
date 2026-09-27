"""Shared helpers for the one-time snapshot scripts. This directory is the only
place (besides bootstrap/) allowed to touch the network."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).resolve().parent
DATA_DIR = SNAPSHOT_DIR.parent
TILES_DIR = DATA_DIR / "tiles"
MANIFEST_PATH = SNAPSHOT_DIR / "MANIFEST.json"
AOI = json.loads((DATA_DIR / "aoi.json").read_text())

# Raw downloads (multi-GB) live outside the repo; override with APERTURE_CACHE_DIR.
CACHE_DIR = Path(os.environ.get("APERTURE_CACHE_DIR", SNAPSHOT_DIR / ".cache"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, expected_size: int | None = None) -> Path:
    """Resumable download via HTTP Range into dest.part, renamed on completion.
    An existing dest is treated as the snapshot and never re-fetched."""
    import httpx  # imported lazily so offline scripts can reuse the manifest helpers

    if dest.exists():
        print(f"  cached  {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as r:
        if r.status_code != 416:  # 416: .part already holds the whole file
            r.raise_for_status()
            mode = "ab" if r.status_code == 206 else "wb"
            total = int(r.headers.get("content-length", 0)) + (have if mode == "ab" else 0)
            done = have if mode == "ab" else 0
            next_report = done + (256 << 20)
            with part.open(mode) as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if done >= next_report:
                        print(f"  {dest.name}: {done / 1e9:.2f} / {total / 1e9:.2f} GB", flush=True)
                        next_report += 256 << 20
    if expected_size is not None and part.stat().st_size != expected_size:
        raise RuntimeError(f"{dest.name}: size {part.stat().st_size} != expected {expected_size}")
    part.rename(dest)
    print(f"  done    {dest.name}")
    return dest


def docker_run(image: str, args: list[str], mounts: dict[Path, str]) -> str:
    """Run a bootstrap tool image with host dirs mounted; returns stdout."""
    import subprocess

    cmd = ["docker", "run", "--rm"]
    for host, container in mounts.items():
        cmd += ["-v", f"{host.resolve().as_posix()}:{container}"]
    cmd += [image, *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def in_bbox(lon: float, lat: float) -> bool:
    w, s, e, n = AOI["bbox"]
    return w <= lon <= e and s <= lat <= n


def update_manifest(section: str, entry: dict) -> None:
    """Record provenance (sources) and outputs (sha256, sizes, counts) for one dataset."""
    manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {}
    entry["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    manifest[section] = entry
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def describe_output(path: Path) -> dict:
    return {
        "path": path.relative_to(DATA_DIR).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
