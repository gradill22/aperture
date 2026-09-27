"""Phase gate runner.

uv run python scripts/verify.py phase0
uv run python scripts/verify.py phase1 [--record]    (--record rewrites the golden JSON)
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "scripts" / "golden"
# Git Bash would rewrite container paths like /dev/null in docker args.
ENV = os.environ | {"MSYS_NO_PATHCONV": "1"}


def run(*args: str) -> bool:
    print(f"$ {' '.join(args)}", flush=True)
    return subprocess.run(args, cwd=ROOT, env=ENV, check=False).returncode == 0


def probe(*curl_args: str) -> subprocess.CompletedProcess:
    """curl from a throwaway container on the internal network."""
    cmd = ["docker", "compose", "run", "--rm", "-T", "probe", *curl_args]
    return subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, check=False)


def phase0() -> bool:
    fixtures = ["aircraft.parquet", "positions.parquet", "fixtures.json"] + [
        f"{layer}.geojsonl" for layer in ("airports", "ports", "government", "military")
    ]
    missing = [f for f in fixtures if not (ROOT / "data" / "fixtures" / f).exists()]
    if missing:
        print(f"  missing fixtures: {missing}")
    return all([
        run(sys.executable, "data/snapshot/verify.py"),
        run(sys.executable, "scripts/lint_network.py"),
        not missing,
    ])  # fmt: skip


# Must all fail from inside aperture-internal: raw IP, DNS name, and the Docker host.
EGRESS_TARGETS = ["https://1.1.1.1", "http://example.com", "http://host.docker.internal:1234"]

DCA = 246  # osm_feature id of KDCA (ids are deterministic for a given snapshot load)
DC_CORE = "-77.12,38.80,-76.90,38.99"
H12 = {"start": "2026-09-24T12:00:00Z", "end": "2026-09-24T13:00:00Z"}
H14 = {"start": "2026-09-24T14:00:00Z", "end": "2026-09-24T15:00:00Z"}
BOX = [[-77.05, 38.84], [-77.03, 38.84], [-77.03, 38.86], [-77.05, 38.86], [-77.05, 38.84]]
GOLDEN_CASES = {
    "health": ("GET", "/health", None),
    "search_feature": ("GET", "/entities?q=Reagan%20National&kind=feature&limit=5", None),
    "search_aircraft": ("GET", "/entities?q=N101HQ", None),
    "aircraft": ("GET", "/entities/aircraft/a00929", None),
    "feature": ("GET", f"/entities/feature/{DCA}", None),
    "track": ("GET", "/tracks/a00929?start=2026-09-24T14:00:00Z&end=2026-09-24T15:00:00Z", None),
    "replay": (
        "GET",
        f"/replay?t0=2026-09-24T14:00:00Z&t1=2026-09-24T14:10:00Z&step=60&bbox={DC_CORE}",
        None,
    ),
    "geofence_feature": (
        "POST",
        "/geofence",
        {"feature_id": DCA, "buffer_m": 2000, "include_geometry": False} | H14,
    ),
    "geofence_polygon": (
        "POST",
        "/geofence",
        {"polygon": {"type": "Polygon", "coordinates": [BOX]}, "include_geometry": False} | H12,
    ),
}
VOLATILE = {"loaded_at"}


def fetch(method: str, path: str, body: dict | None) -> tuple[int, object]:
    args = ["-s", "-m", "60", "-w", "\n%{http_code}", f"http://backend:8000{path}"]
    if method == "POST":
        args += ["-H", "content-type: application/json", "-d", json.dumps(body)]
    out = probe(*args).stdout
    payload, _, status = out.rpartition("\n")
    data = json.loads(payload) if payload else None
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in VOLATILE}
    return int(status or 0), data


def wait_healthy(timeout_s: int = 180) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if fetch("GET", "/health", None)[0] == 200:
            return True
        time.sleep(3)
    return False


def phase1(record: bool = False) -> bool:
    ok = run(sys.executable, "scripts/lint_network.py")
    ok &= run("uv", "run", "ruff", "check", ".")
    ok &= run("docker", "compose", "--profile", "test", "up", "-d", "--wait", "db-test")
    ok &= run("uv", "run", "pytest", "-q")
    # `up` blocks until the one-shot loader has completed (backend depends on it).
    ok &= run("docker", "compose", "up", "-d", "--build")
    if not wait_healthy():
        print("  backend never became healthy")
        return False

    print("egress probes (each must fail):", flush=True)
    for url in EGRESS_TARGETS:
        res = probe("-sS", "-m", "5", "-o", "/dev/null", url)
        blocked = res.returncode != 0
        print(f"  {url}: {'blocked' if blocked else 'REACHABLE'} (curl exit {res.returncode})")
        ok &= blocked

    print("golden responses:", flush=True)
    folder = GOLDEN / "phase1"
    folder.mkdir(parents=True, exist_ok=True)
    for name, (method, path, body) in GOLDEN_CASES.items():
        status, data = fetch(method, path, body)
        file = folder / f"{name}.json"
        if record and status == 200:
            file.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        expected = json.loads(file.read_text(encoding="utf-8")) if file.exists() else None
        match = status == 200 and data == expected
        print(f"  {name}: HTTP {status} {'match' if match else 'MISMATCH'}")
        ok &= match
    return ok


GATES = {"phase0": phase0, "phase1": phase1}

if __name__ == "__main__":
    gate = sys.argv[1] if len(sys.argv) > 1 else ""
    if gate not in GATES:
        sys.exit(__doc__)
    ok = GATES[gate](record=True) if "--record" in sys.argv else GATES[gate]()
    print(f"\n{gate}: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
