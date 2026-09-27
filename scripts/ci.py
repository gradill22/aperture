"""Local CI stack: Gitea + act_runner (ci/docker-compose.yml), offline.

    uv run python scripts/ci.py up       seed the build job's data volume, start Gitea (+ runner)
    uv run python scripts/ci.py down     stop the stack (Gitea's data volumes are kept)
    uv run python scripts/ci.py seed     (re)copy the snapshot artifacts into aperture-ci-data

The runner starts once it can register: either it already has /data/.runner from an earlier
registration, or RUNNER_TOKEN is set in ci/.env (you create that token in the Gitea UI).
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "ci"
COMPOSE = ["docker", "compose", "-f", str(CI_DIR / "docker-compose.yml")]
DATA_VOLUME = "aperture-ci-data"
RUNNER_VOLUME = "aperture-ci_runner-data"
TOOL_IMAGE = "aperture/ci-python:latest"  # any local image with sh/cp; never pulled
# Gitignored snapshot artifacts the app images are built from (see .dockerignore).
SEED = {
    "snapshot": ["snapshot/adsb", "snapshot/osm"],
    "tiles": ["tiles/basemap.pmtiles", "tiles/infrastructure.pmtiles", "tiles/fonts", "tiles/sprites"],
}  # fmt: skip


def sh(image_args: list[str], script: str) -> subprocess.CompletedProcess:
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        *image_args,
        TOOL_IMAGE,
        "sh",
        "-c",
        script,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def manifest_id() -> str:
    return hashlib.sha256((ROOT / "data" / "snapshot" / "MANIFEST.json").read_bytes()).hexdigest()


def seed(force: bool = False) -> bool:
    """Copy data/ artifacts into the named volume the build job mounts (skipped when current)."""
    want = manifest_id()
    have = sh(["-v", f"{DATA_VOLUME}:/dst"], "cat /dst/.manifest-sha256 2>/dev/null").stdout.strip()
    if have == want and not force:
        print(f"  {DATA_VOLUME}: current (MANIFEST {want[:12]})", flush=True)
        return True
    if subprocess.run(
        [sys.executable, "data/snapshot/verify.py"], cwd=ROOT, check=False
    ).returncode:
        print("  local snapshot does not match MANIFEST.json: not seeding")
        return False
    copies = " && ".join(
        f"mkdir -p /dst/{d} && cp -r {' '.join(f'/src/{p}' for p in paths)} /dst/{d}/"
        for d, paths in SEED.items()
    )
    script = f"rm -rf /dst/* && {copies} && echo {want} > /dst/.manifest-sha256 && du -sh /dst"
    res = sh(["-v", f"{DATA_VOLUME}:/dst", "-v", f"{ROOT / 'data'}:/src:ro"], script)
    print(
        f"  {DATA_VOLUME}: seeded {res.stdout.strip()}" if res.returncode == 0 else res.stderr,
        flush=True,
    )
    return res.returncode == 0


def env_value(key: str) -> str:
    """A value from ci/.env (which you create; gitignored), or ""."""
    env = CI_DIR / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return ""


def gitea_url() -> str:
    return f"http://localhost:{os.environ.get('GITEA_PORT') or env_value('GITEA_PORT') or '3000'}"


def git_out(*args: str) -> str:
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def gitea_repo() -> str | None:
    """owner/repo of the `gitea` remote."""
    m = re.match(
        r"^https?://[^/]+/([^/]+/[^/]+?)(?:\.git)?/?$", git_out("remote", "get-url", "gitea")
    )
    return m.group(1) if m else None


def gitea_api(path: str) -> tuple[int, str]:
    """GET the local Gitea API with the host's curl; a read token in ci/.env (GITEA_READ_TOKEN)
    is sent if present, on stdin so it never appears in a process list."""
    token = env_value("GITEA_READ_TOKEN")
    cmd = ["curl", "-s", "-m", "30", "-w", "\n%{http_code}", "-H", "@-", f"{gitea_url()}/api/v1{path}"]  # fmt: skip
    header = f"Authorization: token {token}\n" if token else ""
    res = subprocess.run(cmd, input=header, capture_output=True, text=True, encoding="utf-8",
                         check=False)  # fmt: skip
    body, _, status = res.stdout.rpartition("\n")
    return int(status or 0), body


def latest_run(repo: str, sha: str) -> tuple[int, dict | None]:
    """(HTTP status, the newest push-triggered Actions run for `sha` or None)."""
    status, body = gitea_api(f"/repos/{repo}/actions/runs?head_sha={sha}&event=push")
    if status != 200:
        return status, None
    runs = [r for r in json.loads(body).get("workflow_runs") or [] if r.get("head_sha") == sha]
    return status, max(runs, key=lambda r: r["id"]) if runs else None


def runner_registered() -> bool:
    # Mounting a missing volume would create it outside Compose (no project labels): look first.
    exists = subprocess.run(["docker", "volume", "inspect", RUNNER_VOLUME],
                            capture_output=True, check=False).returncode == 0  # fmt: skip
    return (
        exists and sh(["-v", f"{RUNNER_VOLUME}:/data:ro"], "test -s /data/.runner").returncode == 0
    )


def up() -> bool:
    print("ci data volume:", flush=True)
    if not seed():
        return False
    services = ["gitea", "gitea-edge"]
    if runner_registered() or env_value("RUNNER_TOKEN"):
        services.append("runner")
    else:
        print("  runner not started: not registered yet and no RUNNER_TOKEN in ci/.env", flush=True)
    ok = subprocess.run([*COMPOSE, "up", "-d", "--wait", *services], check=False).returncode == 0
    print("Gitea: http://localhost:3000" if ok else "CI stack failed to start")
    return ok


def down() -> bool:
    return subprocess.run([*COMPOSE, "down"], check=False).returncode == 0


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    commands = {"up": up, "down": down, "seed": lambda: seed(force=True)}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    sys.exit(0 if commands[sys.argv[1]]() else 1)
