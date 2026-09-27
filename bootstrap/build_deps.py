"""Build the dependency images (networked; run after any lockfile change).

    uv run python bootstrap/build_deps.py [py|node|e2e ...]     (default: all)

Tags aperture/<name>-deps:<lock hash> and :latest; the hash covers the lockfile and manifests
it was built from, and is stored as the image label aperture.lock-sha256.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    "py": (
        "bootstrap/deps/python.Dockerfile",
        [
            "pyproject.toml", "uv.lock", "data/pyproject.toml", "db/loader/pyproject.toml",
            "backend/pyproject.toml", "mcp-server/pyproject.toml",
        ],
    ),
    "node": ("bootstrap/deps/node.Dockerfile", ["frontend/package.json", "frontend/package-lock.json"]),
    "e2e": ("bootstrap/deps/e2e.Dockerfile", ["e2e/package.json", "e2e/package-lock.json"]),
}  # fmt: skip


def lock_hash(files: list[str]) -> str:
    h = hashlib.sha256()
    for name in files:
        h.update(name.encode() + b"\0" + (ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def build(name: str) -> int:
    dockerfile, inputs = IMAGES[name]
    digest = lock_hash(inputs)
    tag = f"aperture/{name}-deps"
    cmd = [
        "docker", "build", "--load", "-f", dockerfile, "--build-arg", f"LOCK_SHA256={digest}",
        "-t", f"{tag}:{digest[:12]}", "-t", f"{tag}:latest", ".",
    ]  # fmt: skip
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


def main() -> None:
    names = sys.argv[1:] or list(IMAGES)
    unknown = set(names) - set(IMAGES)
    if unknown:
        sys.exit(f"unknown deps image(s): {', '.join(sorted(unknown))}; choose from {', '.join(IMAGES)}")
    for name in names:
        if rc := build(name):
            sys.exit(rc)


if __name__ == "__main__":
    main()
