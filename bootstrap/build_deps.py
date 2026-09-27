"""Build the dependency images (networked; run after any lockfile change).

    uv run python bootstrap/build_deps.py [py|node|e2e|ci-python|ci-node ...]   (default: all)
    python bootstrap/build_deps.py --check     offline: every local image matches its lockfiles

Tags <image>:<lock hash> and :latest (aperture/<name>-deps, or aperture/<name> for the CI job
images); the hash covers the lockfiles and manifests it was built from (plus the Dockerfile for
the CI images) and is stored as the image label aperture.lock-sha256. App builds and CI jobs only
ever use these images, so a stale one means the build is not testing the locked dependencies.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_INPUTS = [
    "pyproject.toml", "uv.lock", "data/pyproject.toml", "db/loader/pyproject.toml",
    "backend/pyproject.toml", "mcp-server/pyproject.toml",
]  # fmt: skip
NODE_INPUTS = ["frontend/package.json", "frontend/package-lock.json"]
CI_SHARED = ["bootstrap/deps/ci-checkout.sh"]
# name -> (Dockerfile, hashed inputs); built in this order (ci-node is FROM node-deps)
IMAGES = {
    "py": ("bootstrap/deps/python.Dockerfile", PY_INPUTS),
    "node": ("bootstrap/deps/node.Dockerfile", NODE_INPUTS),
    "e2e": ("bootstrap/deps/e2e.Dockerfile", ["e2e/package.json", "e2e/package-lock.json"]),
    "ci-python": (
        "bootstrap/deps/ci-python.Dockerfile",
        [*PY_INPUTS, "bootstrap/deps/ci-python.Dockerfile", *CI_SHARED],
    ),
    "ci-node": (
        "bootstrap/deps/ci-node.Dockerfile",
        [*NODE_INPUTS, "bootstrap/deps/ci-node.Dockerfile", *CI_SHARED],
    ),
}


def image(name: str) -> str:
    return f"aperture/{name}" if name.startswith("ci-") else f"aperture/{name}-deps"


def lock_hash(files: list[str]) -> str:
    h = hashlib.sha256()
    for name in files:
        h.update(name.encode() + b"\0" + (ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def build(name: str) -> int:
    dockerfile, inputs = IMAGES[name]
    digest = lock_hash(inputs)
    tag = image(name)
    cmd = [
        "docker", "build", "--load", "-f", dockerfile, "--build-arg", f"LOCK_SHA256={digest}",
        "-t", f"{tag}:{digest[:12]}", "-t", f"{tag}:latest", ".",
    ]  # fmt: skip
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


def check() -> int:
    stale = 0
    for name, (_, inputs) in IMAGES.items():
        want = lock_hash(inputs)
        fmt = '{{index .Config.Labels "aperture.lock-sha256"}}'
        res = subprocess.run(["docker", "image", "inspect", "--format", fmt, f"{image(name)}:latest"],
                             capture_output=True, text=True, check=False)  # fmt: skip
        have = res.stdout.strip() if res.returncode == 0 else "missing"
        ok = have == want
        stale += not ok
        print(
            f"  {image(name)}:latest  lock {want[:12]}  {'ok' if ok else f'STALE (image: {have[:12]})'}"
        )
    if stale:
        print("run bootstrap: uv run python bootstrap/build_deps.py <name> (networked)")
    return 1 if stale else 0


def main() -> None:
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    names = sys.argv[1:] or list(IMAGES)
    unknown = set(names) - set(IMAGES)
    if unknown:
        sys.exit(
            f"unknown deps image(s): {', '.join(sorted(unknown))}; choose from {', '.join(IMAGES)}"
        )
    for name in names:
        if rc := build(name):
            sys.exit(rc)


if __name__ == "__main__":
    main()
