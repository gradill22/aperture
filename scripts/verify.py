"""Phase gate runner.

uv run python scripts/verify.py phase0
uv run python scripts/verify.py phase1 [--record]    (--record rewrites the golden JSON)
uv run python scripts/verify.py phase2               (needs LM Studio serving qwen/qwen3.5-9b)
uv run python scripts/verify.py phase3               (APERTURE_PORT=<port> if 8080 is taken)
"""

import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
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
NO_PYTHON = {"tiles"}


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


def lint_test_up() -> bool:
    ok = run(sys.executable, "scripts/lint_network.py")
    ok &= run("uv", "run", "ruff", "check", ".")
    ok &= run("docker", "compose", "--profile", "test", "up", "-d", "--wait", "db-test")
    ok &= run("uv", "run", "pytest", "-q")
    # `up` blocks until the one-shot loader has completed (backend depends on it).
    ok &= run("docker", "compose", "up", "-d", "--build")
    if not wait_healthy():
        print("  backend never became healthy")
        return False
    return ok


def egress_blocked(services: tuple[str, ...] = ()) -> bool:
    """Every target must be unreachable from the probe container and from each named service."""
    ok = True
    print("egress probes (each must fail):", flush=True)
    for url in EGRESS_TARGETS:
        res = probe("-sS", "-m", "5", "-o", "/dev/null", url)
        blocked = res.returncode != 0
        print(f"  probe -> {url}: {'blocked' if blocked else 'REACHABLE'} (exit {res.returncode})")
        ok &= blocked
        for svc in services:
            if svc in NO_PYTHON:  # images without Python: busybox/GNU wget
                cmd = [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    svc,
                    "wget",
                    "-q",
                    "-T",
                    "5",
                    "-O",
                    "/dev/null",
                    url,
                ]
            else:
                code = f"import urllib.request as u; u.urlopen({url!r}, timeout=5)"
                cmd = ["docker", "compose", "exec", "-T", svc, "python", "-c", code]
            res = subprocess.run(
                cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, check=False
            )
            blocked = res.returncode != 0
            print(f"  {svc} -> {url}: {'blocked' if blocked else 'REACHABLE'}")
            ok &= blocked
    return ok


def phase1(record: bool = False) -> bool:
    ok = lint_test_up()
    if not ok:
        return False
    ok &= egress_blocked()

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


# --------------------------------------------------------------------------- phase 2

LLM_MODEL = "qwen/qwen3.5-9b"
# Geofence questions: the model may fence any feature whose name matches; buffer and window must
# be exactly what was asked. Ground truth is recomputed for the fence it chose.
FENCE_QUESTIONS = {
    "q1_dca": {
        "question": "Which aircraft came within 2 km of Reagan National between 14:00 and 15:00 "
        "UTC on Sep 24?",
        "name_like": "%Reagan%",
        "buffer_m": 2000,
        "window": ("2026-09-24T14:00:00+00:00", "2026-09-24T15:00:00+00:00"),
    },
    "q3_andrews": {
        "question": "List the aircraft within 3 km of Joint Base Andrews (KADW) from 15:00 to "
        "16:00 UTC on 24 September 2026.",
        "name_like": "%Andrews%",
        "buffer_m": 3000,
        "window": ("2026-09-24T15:00:00+00:00", "2026-09-24T16:00:00+00:00"),
    },
}
TRACK_QUESTION = {"question": "Show me the flight track of N101HQ.", "icao24": "a00929"}
# Truth bounds straddle the fence edge by this much: ST_Buffer's polygonal circle vs exact distance.
EDGE_M = 10
# Planar prefilter radius in degrees; must exceed buffer_m + EDGE_M at this latitude (1 deg lon
# ~ 86 km at 39N, so 0.06 deg covers > 5 km).
PREFILTER_DEG = 0.06

# Report-level lower bound: every position report (on a flight leg) inside the buffer during the
# window must be flagged.
POINT_TRUTH = """
SELECT DISTINCT p.icao24 FROM adsb_position p, osm_feature f
WHERE f.id = {fid} AND p.ts BETWEEN '{t0}' AND '{t1}'
  AND ST_DWithin(p.geom, f.geom, {deg})
  AND ST_DWithin(p.geom::geography, f.geom::geography, {buf} - {edge})
  AND EXISTS (SELECT 1 FROM flight fl
              WHERE fl.icao24 = p.icao24 AND p.ts BETWEEN fl.start_ts AND fl.end_ts)
"""
# Segment-level upper bound: nothing may be flagged unless the straight line between two
# consecutive reports (at least partly in the window) passes within the buffer. (Reports more than
# 10 min apart start a new leg and are never joined, so a 15 min lookback suffices.)
SEGMENT_TRUTH = """
WITH seg AS (
  SELECT icao24, ts, geom, lag(ts) OVER w AS prev_ts, lag(geom) OVER w AS prev_geom
  FROM adsb_position
  WHERE ts BETWEEN timestamptz '{t0}' - interval '15 min' AND timestamptz '{t1}' + interval '15 min'
  WINDOW w AS (PARTITION BY icao24 ORDER BY ts)
)
SELECT DISTINCT s.icao24 FROM seg s, osm_feature f
WHERE f.id = {fid} AND s.prev_geom IS NOT NULL AND s.ts >= '{t0}' AND s.prev_ts <= '{t1}'
  AND ST_DWithin(ST_MakeLine(s.prev_geom, s.geom), f.geom, {deg})
  AND ST_DWithin(ST_MakeLine(s.prev_geom, s.geom)::geography, f.geom::geography, {buf} + {edge})
"""


def sql(query: str) -> list[str]:
    cmd = ["docker", "compose", "exec", "-T", "db", "psql", "-U", "aperture", "-d", "aperture",
           "-v", "ON_ERROR_STOP=1", "-At", "-c", query]  # fmt: skip
    res = subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, check=True)
    return [line for line in res.stdout.splitlines() if line]


def post_json(url: str, body: dict, *headers: str, timeout_s: int = 300) -> tuple[int, dict | None]:
    args = ["-s", "-m", str(timeout_s), "-w", "\n%{http_code}", "-X", "POST", url,
            "-H", "content-type: application/json", "-d", json.dumps(body)]  # fmt: skip
    for h in headers:
        args += ["-H", h]
    out = probe(*args).stdout
    payload, _, status = out.rpartition("\n")
    try:
        return int(status or 0), json.loads(payload) if payload else None
    except json.JSONDecodeError:
        return int(status or 0), None


def ask(name: str, question: str) -> dict | None:
    t = time.monotonic()
    status, out = post_json(
        "http://backend:8000/chat", {"messages": [{"role": "user", "content": question}]}
    )
    print(f"  {name}: HTTP {status} in {time.monotonic() - t:.0f}s", flush=True)
    if status != 200 or not out:
        print(f"    {out}")
        return None
    evidence = ROOT / ".verify" / "phase2"
    evidence.mkdir(parents=True, exist_ok=True)
    slim = {"question": question, "reply": out["reply"], "tool_calls": out["tool_calls"]}
    (evidence / f"{name}.json").write_text(json.dumps(slim, indent=1) + "\n", encoding="utf-8")
    for call in out["tool_calls"]:
        print(f"    tool {call['name']} {json.dumps(call['arguments'])} ok={call['ok']}")
    print("    reply: " + out["reply"].replace("\n", "\n           "))
    return out


def mentions_any(reply: str, hits: list[dict]) -> bool:
    ids = {v for h in hits for k in ("icao24", "registration", "callsign") if (v := h.get(k))}
    return any(i.lower() in reply.lower() for i in ids)


def parse_ts(value: str | None) -> datetime | None:
    """Tool time argument as the backend reads it (naive = display tz, UTC for these asks)."""
    if not value:
        return None
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def check_fence_answer(spec: dict, out: dict) -> bool:
    calls = [c for c in out["tool_calls"] if c["name"] == "geofence_alert" and c["ok"]]
    if not calls:
        print("    FAIL: no successful geofence_alert call")
        return False
    args = calls[-1]["arguments"]
    allowed = {
        int(i) for i in sql(f"SELECT id FROM osm_feature WHERE name ILIKE '{spec['name_like']}'")
    }
    t0, t1 = spec["window"]
    window = (parse_ts(args.get("start")), parse_ts(args.get("end")))
    expected = (parse_ts(t0), parse_ts(t1))
    checks = (
        (f"feature {args.get('feature_id')} is a {spec['name_like']} feature",
         args.get("feature_id") in allowed),
        (f"buffer_m {args.get('buffer_m')} == {spec['buffer_m']}",
         float(args.get("buffer_m") or 0) == spec["buffer_m"]),
        (f"window {args.get('start')} .. {args.get('end')} == {t0} .. {t1}", window == expected),
    )  # fmt: skip
    for label, good in checks:
        print(f"    {'ok' if good else 'FAIL'}: {label}")
    if not all(good for _, good in checks):
        return False

    hits = [h for a in out["map_actions"] if a["type"] == "show_geofence_hits" for h in a["hits"]]
    answer = {h["icao24"] for h in hits}
    q = {
        "fid": args["feature_id"],
        "t0": t0,
        "t1": t1,
        "buf": spec["buffer_m"],
        "edge": EDGE_M,
        "deg": PREFILTER_DEG,
    }
    lower = set(sql(POINT_TRUTH.format(**q)))
    upper = set(sql(SEGMENT_TRUTH.format(**q)))
    missing, extra = lower - answer, answer - upper
    good = bool(answer) and not missing and not extra
    print(f"    {'ok' if good else 'FAIL'}: {len(lower)} by reports <= {len(answer)} answered"
          f" <= {len(upper)} by segments")  # fmt: skip
    if missing or extra:
        print(f"      missing {sorted(missing)} unexpected {sorted(extra)}")
    named = mentions_any(out["reply"], hits)
    print(f"    {'ok' if named else 'FAIL'}: reply names at least one of the aircraft")
    return good and named


def check_track_answer(spec: dict, out: dict) -> bool:
    shown = [a for a in out["map_actions"] if a["type"] == "show_track"]
    icaos = {a["icao24"] for a in shown}
    n_legs = int(sql(f"SELECT count(*) FROM flight WHERE icao24 = '{spec['icao24']}'")[0])
    legs = {leg["leg"] for a in shown for leg in a["legs"]}
    reply = out["reply"].lower()
    checks = (
        (f"tracks shown for {sorted(icaos)} == ['{spec['icao24']}']", icaos == {spec["icao24"]}),
        (f"{len(legs)} legs drawn == {n_legs} in SQL", len(legs) == n_legs),
        ("reply names the aircraft", spec["icao24"] in reply or "n101hq" in reply),
    )
    for label, good in checks:
        print(f"    {'ok' if good else 'FAIL'}: {label}")
    return all(good for _, good in checks)


MCP_URL = "http://mcp-server:8001/mcp"
MCP_ACCEPT = "accept: application/json, text/event-stream"


def check_mcp() -> bool:
    _, listed = post_json(MCP_URL, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, MCP_ACCEPT)
    names = sorted(t["name"] for t in (listed or {}).get("result", {}).get("tools", []))
    ok = names == ["geofence_alert", "get_entity_track", "search_entities"]
    print(f"  mcp tools/list: {names} {'ok' if ok else 'FAIL'}")
    params = {"name": "search_entities", "arguments": {"query": "KDCA", "kind": "feature"}}
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params}
    _, called = post_json(MCP_URL, call, MCP_ACCEPT)
    try:
        first = json.loads(called["result"]["content"][0]["text"])["results"][0]["feature_id"]
    except TypeError, KeyError, IndexError, json.JSONDecodeError:
        first = None
    good = first == DCA
    print(f"  mcp tools/call search_entities KDCA -> feature {first} {'ok' if good else 'FAIL'}")
    return ok and good


def phase2() -> bool:
    ok = lint_test_up()
    if not ok:
        return False
    ok &= egress_blocked(services=("backend", "mcp-server"))

    res = probe("-sS", "-m", "10", "http://llm-relay:1234/v1/models")
    relay = res.returncode == 0 and LLM_MODEL in res.stdout
    print(f"  llm-relay -> LM Studio: {'ok, serving ' + LLM_MODEL if relay else 'FAIL'}")
    ok &= relay
    ok &= check_mcp()
    if not relay:
        return False

    print("chat (live LLM; answers checked against SQL ground truth):", flush=True)
    for name, spec in FENCE_QUESTIONS.items():
        out = ask(name, spec["question"])
        ok &= out is not None and check_fence_answer(spec, out)
    out = ask("q2_track", TRACK_QUESTION["question"])
    ok &= out is not None and check_track_answer(TRACK_QUESTION, out)
    return ok


# --------------------------------------------------------------------------- phase 3

EDGE_UPSTREAMS = {"backend:8000", "tiles:3000", "mcp-server:8001"}
EVIDENCE3 = ROOT / ".verify" / "phase3"


def compose_out(*args: str) -> str:
    cmd = ["docker", "compose", *args]
    return subprocess.run(
        cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, check=False
    ).stdout


def edge_config_ok() -> bool:
    """The edge's effective nginx config may only proxy to internal services, never resolve names."""
    conf = compose_out("exec", "-T", "frontend", "nginx", "-T")
    passes = re.findall(r"^\s*(\w+_pass)\s+([^;]+);", conf, re.MULTILINE)
    targets = {re.sub(r"^\w+://", "", t).split("/")[0] for _, t in passes}
    resolvers = re.findall(r"^\s*resolver\s", conf, re.MULTILINE)
    ok = bool(conf) and targets <= EDGE_UPSTREAMS and not resolvers
    print(
        f"  edge nginx upstreams: {sorted(targets)}; resolver directives: {len(resolvers)} {'ok' if ok else 'FAIL'}"
    )
    return ok


def internal_subnets() -> list[ipaddress.IPv4Network]:
    out = subprocess.run(
        ["docker", "network", "inspect", "aperture-internal", "--format", "{{range .IPAM.Config}}{{.Subnet}} {{end}}"],
        capture_output=True, text=True, check=False,
    ).stdout.split()  # fmt: skip
    return [ipaddress.ip_network(n) for n in out if ":" not in n]


def edge_sockets_ok() -> bool:
    """After the e2e run: every connection the edge opened (not accepted on :8080) went to the internal network."""
    table = compose_out("exec", "-T", "frontend", "cat", "/proc/net/tcp")
    nets = internal_subnets()
    outbound, stray = 0, []
    for line in table.splitlines()[1:]:
        f = line.split()
        lport = f[1].split(":")[1]
        rip, rport = f[2].split(":")
        state = f[3]
        if state == "0A" or int(lport, 16) == 8080:  # listening socket / inbound clients
            continue
        addr = ipaddress.IPv4Address(bytes.fromhex(rip)[::-1])
        outbound += 1
        if not addr.is_loopback and not any(addr in n for n in nets):
            stray.append(f"{addr}:{int(rport, 16)}")
    ok = bool(nets) and outbound > 0 and not stray
    print(
        f"  edge outbound sockets: {outbound}, outside {[str(n) for n in nets]}: {stray or 'none'} {'ok' if ok else 'FAIL'}"
    )
    return ok


def relay_config_ok() -> bool:
    env = compose_out("exec", "-T", "llm-relay", "printenv", "LLM_UPSTREAM").strip()
    ok = env == "host.docker.internal:1234"
    print(f"  llm-relay upstream: {env or '?'} {'ok' if ok else 'FAIL'}")
    return ok


def e2e() -> bool:
    out = EVIDENCE3 / "e2e"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.png"):
        f.unlink()
    ok = run("docker", "compose", "--profile", "tools", "build", "e2e")
    ok &= run("docker", "compose", "run", "--rm", "e2e")
    try:
        stats = json.loads((out / "results.json").read_text())["stats"]
        print(
            f"  playwright: {stats['expected']} passed, {stats['unexpected']} failed, {stats['flaky']} flaky"
        )
        ok &= stats["unexpected"] == 0 and stats["expected"] >= 4
    except OSError, KeyError, json.JSONDecodeError:
        print("  playwright: no results.json")
        ok = False
    return ok


def phase3() -> bool:
    ok = run(
        "docker",
        "build",
        "--network",
        "none",
        "--target",
        "check",
        "-f",
        "frontend/Dockerfile",
        ".",
    )
    ok &= lint_test_up()
    if not ok:
        return False
    print("offline boundary:", flush=True)
    ok &= egress_blocked(services=("backend", "mcp-server", "tiles"))
    ok &= edge_config_ok()
    ok &= relay_config_ok()
    ok &= check_mcp()
    print("browser (Playwright on the internal network):", flush=True)
    ok &= e2e()
    ok &= edge_sockets_ok()
    return ok


GATES = {"phase0": phase0, "phase1": phase1, "phase2": phase2, "phase3": phase3}

if __name__ == "__main__":
    gate = sys.argv[1] if len(sys.argv) > 1 else ""
    if gate not in GATES:
        sys.exit(__doc__)
    ok = GATES[gate](record=True) if "--record" in sys.argv else GATES[gate]()
    print(f"\n{gate}: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
