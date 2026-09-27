"""Build the dependency images (networked; run after any lockfile change).

    uv run python bootstrap/build_deps.py

Tags aperture/py-deps:<lock hash> and :latest; the hash covers uv.lock and every
workspace pyproject.toml, and is stored as the image label aperture.lock-sha256.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_INPUTS = [
    "pyproject.toml", "uv.lock", "data/pyproject.toml", "db/loader/pyproject.toml",
    "backend/pyproject.toml",
]  # fmt: skip


def lock_hash(files: list[str]) -> str:
    h = hashlib.sha256()
    for name in files:
        h.update(name.encode() + b"\0" + (ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def main() -> None:
    digest = lock_hash(PY_INPUTS)
    cmd = [
        "docker", "build", "--load", "-f", "bootstrap/deps/python.Dockerfile",
        "--build-arg", f"LOCK_SHA256={digest}",
        "-t", f"aperture/py-deps:{digest[:12]}", "-t", "aperture/py-deps:latest", ".",
    ]  # fmt: skip
    print("$", " ".join(cmd), flush=True)
    sys.exit(subprocess.run(cmd, cwd=ROOT, check=False).returncode)


if __name__ == "__main__":
    main()
