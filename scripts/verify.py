"""Phase gate runner.

uv run python scripts/verify.py phase0
uv run python scripts/verify.py phase1 [--record]    (--record rewrites the golden JSON)
uv run python scripts/verify.py phase2               (needs LM Studio serving qwen/qwen3.5-9b)
uv run python scripts/verify.py phase3               (APERTURE_PORT=<port> if 8090 is taken)
uv run python scripts/verify.py phase4               (minikube profile `aperture` running; LM Studio)
uv run python scripts/verify.py phase5               (Gitea set up, runner registered, `gitea` remote)
"""

import configparser
import io
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import k8s

import ci

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "scripts" / "golden"
# Git Bash would rewrite container paths like /dev/null in docker args.
ENV = os.environ | {"MSYS_NO_PATHCONV": "1"}
# Where probe/sql/exec run: the compose stack, or (phase 4) the minikube namespace.
TARGET = "compose"


def run(*args: str) -> bool:
    print(f"$ {' '.join(args)}", flush=True)
    return subprocess.run(args, cwd=ROOT, env=ENV, check=False).returncode == 0


def probe(*curl_args: str) -> subprocess.CompletedProcess:
    """curl from inside the isolated network: a throwaway compose container, or the probe pod."""
    if TARGET == "k8s":
        cmd = k8s.kubectl("exec", "probe", "--", "curl", *curl_args)
    else:
        cmd = ["docker", "compose", "run", "--rm", "-T", "probe", *curl_args]
    return subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, check=False)


def exec_in(svc: str, *args: str) -> list[str]:
    """Command line that runs `args` inside a running service."""
    if TARGET == "k8s":
        return k8s.kubectl("exec", f"deploy/{svc}", "--", *args)
    return ["docker", "compose", "exec", "-T", svc, *args]


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
NO_PYTHON = {"tiles", "frontend"}


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


def egress_blocked(
    services: tuple[str, ...] = (), targets: list[str] = EGRESS_TARGETS, from_probe: bool = True
) -> bool:
    """Every target must be unreachable from the probe container and from each named service."""
    ok = True
    print("egress probes (each must fail):", flush=True)
    for url in targets:
        if from_probe:
            res = probe("-sS", "-m", "5", "-o", "/dev/null", url)
            blocked = res.returncode != 0
            print(
                f"  probe -> {url}: {'blocked' if blocked else 'REACHABLE'} (exit {res.returncode})"
            )
            ok &= blocked
        for svc in services:
            if svc in NO_PYTHON:  # images without Python: busybox/GNU wget
                # GNU wget retries a timed-out connect 20 times (minutes per URL where a
                # NetworkPolicy drops packets); busybox has no --tries. Cap both.
                wget = ("wget", "-q", "-T", "5", "-O", "/dev/null", url)
                cmd = exec_in(svc, "timeout", "10", *wget)
            else:
                code = f"import urllib.request as u; u.urlopen({url!r}, timeout=5)"
                cmd = exec_in(svc, "python", "-c", code)
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
    return ok & golden_ok(record)


def golden_ok(record: bool = False) -> bool:
    """Every backend endpoint answers exactly as recorded from the same snapshot in Gate 1."""
    ok = True
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
    psql = ["psql", "-U", "aperture", "-d", "aperture", "-v", "ON_ERROR_STOP=1", "-At", "-c", query]
    if TARGET == "k8s":
        cmd = k8s.kubectl("exec", "db-0", "-c", "postgres", "--", *psql)
    else:
        cmd = ["docker", "compose", "exec", "-T", "db", *psql]
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


def ask(name: str, question: str, evidence: Path = ROOT / ".verify" / "phase2") -> dict | None:
    t = time.monotonic()
    status, out = post_json(
        "http://backend:8000/chat", {"messages": [{"role": "user", "content": question}]}
    )
    print(f"  {name}: HTTP {status} in {time.monotonic() - t:.0f}s", flush=True)
    if status != 200 or not out:
        print(f"    {out}")
        return None
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
        first = json.loads((called or {})["result"]["content"][0]["text"])["results"][0][
            "feature_id"
        ]
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
        out = ask(name, str(spec["question"]))
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
    return [ipaddress.IPv4Network(n) for n in out if ":" not in n]


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


# --------------------------------------------------------------------------- phase 4

EVIDENCE4 = ROOT / ".verify" / "phase4"
APP_SERVICES = ("loader", "backend", "mcp-server", "llm-relay", "tiles", "frontend")
POLICIES = {"default-deny-egress", "allow-namespace-and-dns", "llm-relay-to-host"}
PF_PORT = 18090
DC_TILE = "10/292/391"  # a z10 tile over downtown DC, present in both tilesets


def init_sql_matches() -> bool:
    """The chart carries its own copy of db/init/ (Helm cannot read outside the chart)."""
    src = {f.name: f.read_bytes() for f in (ROOT / "db" / "init").glob("*.sql")}
    dst = {f.name: f.read_bytes() for f in (k8s.CHART / "files" / "db-init").glob("*.sql")}
    ok = bool(src) and src == dst
    print(
        f"  chart init SQL == db/init/: {'ok' if ok else 'DIFFERS (copy db/init/*.sql into the chart)'}"
    )
    return ok


def chart_ok() -> bool:
    ok = init_sql_matches()
    helm = k8s.tool("helm")
    dummy = ["--set", "llm.hostIP=192.0.2.1"]
    ok &= run(helm, "lint", "--strict", str(k8s.CHART), *dummy)
    res = subprocess.run([helm, "template", "aperture", str(k8s.CHART), *dummy],
                         capture_output=True, text=True, check=False)  # fmt: skip
    (EVIDENCE4 / "rendered.yaml").write_text(res.stdout, encoding="utf-8")
    kinds = sorted(set(re.findall(r"^kind: (\w+)", res.stdout, re.MULTILINE)))
    print(f"  helm template: exit {res.returncode}, kinds {kinds}")
    pulls = re.findall(r"imagePullPolicy: (\w+)", res.stdout)
    never = bool(pulls) and set(pulls) == {"Never"}
    print(f"  imagePullPolicy: {len(pulls)} containers, all Never: {'ok' if never else 'FAIL'}")
    return ok and res.returncode == 0 and never


def start_probe() -> bool:
    subprocess.run(k8s.kubectl("delete", "pod", "probe", "--ignore-not-found", "--wait"),
                   capture_output=True, check=False)  # fmt: skip
    image = k8s.content_tag(k8s.PROBE_IMAGE)
    ok = run(*k8s.kubectl("run", "probe", f"--image={image}", "--image-pull-policy=Never",
                          "--restart=Never", "--labels=app.kubernetes.io/component=probe",
                          "--command", "--", "sleep", "3600"))  # fmt: skip
    return ok and run(*k8s.kubectl("wait", "--for=condition=Ready", "pod/probe", "--timeout=120s"))


def cluster_state_ok() -> bool:
    state = k8s.out(k8s.kubectl("get", "pods,jobs,svc,networkpolicy", "-o", "wide"))
    (EVIDENCE4 / "cluster.txt").write_text(state + "\n", encoding="utf-8")
    print("  " + state.replace("\n", "\n  "))
    names = k8s.out(k8s.kubectl("get", "networkpolicy", "-o", "name")).split()
    have = {n.rpartition("/")[2] for n in names}
    ok = POLICIES <= have
    print(
        f"  network policies {sorted(POLICIES)}: {'ok' if ok else 'MISSING ' + str(POLICIES - have)}"
    )
    return ok


def relay_upstream_ok(host_ip: str) -> bool:
    env = k8s.out(exec_in("llm-relay", "printenv", "LLM_UPSTREAM"))
    ok = env == f"{host_ip}:1234"
    print(f"  llm-relay upstream: {env or '?'} {'ok' if ok else 'FAIL'}")
    return ok


def curl_host(url: str, *args: str) -> tuple[int, str]:
    """curl from the Windows host (not a container): the port-forwarded edge.

    Bytes, not text: tiles are binary protobuf."""
    res = subprocess.run(["curl", "-s", "-m", "30", "-w", "\n%{http_code}", *args, url],
                         capture_output=True, check=False)  # fmt: skip
    body, _, status = res.stdout.rpartition(b"\n")
    return int(status or 0), body.decode("utf-8", errors="replace")


def edge_port_forward_ok() -> bool:
    """The edge Service, reached from the host through kubectl port-forward."""
    pf = subprocess.Popen(k8s.kubectl("port-forward", "svc/frontend", f"{PF_PORT}:8080"),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # fmt: skip
    try:
        line = pf.stdout.readline() if pf.stdout else ""
        if "Forwarding" not in line:
            print(f"  port-forward failed: {line.strip()}")
            return False
        base = f"http://127.0.0.1:{PF_PORT}"
        ok = True
        checks = {
            "/": lambda b: 'id="root"' in b,
            "/api/health": lambda b: '"ok"' in b,
            f"/tiles/basemap/{DC_TILE}": lambda b: len(b) > 0,
            f"/tiles/infrastructure/{DC_TILE}": lambda b: len(b) > 0,
            "/provenance.json": lambda b: "adsb" in b,
        }
        for path, good in checks.items():
            status, body = curl_host(base + path)
            passed = status == 200 and good(body)
            print(f"  edge {path}: HTTP {status} {'ok' if passed else 'FAIL'}")
            ok &= passed
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        status, out = curl_host(f"{base}/mcp", "-X", "POST", "-H", "content-type: application/json",
                                "-H", MCP_ACCEPT, "-d", body)  # fmt: skip
        try:
            names = sorted(t["name"] for t in json.loads(out)["result"]["tools"])
        except KeyError, TypeError, json.JSONDecodeError:
            names = []
        passed = status == 200 and names == [
            "geofence_alert",
            "get_entity_track",
            "search_entities",
        ]
        print(f"  edge /mcp tools/list: HTTP {status} {names} {'ok' if passed else 'FAIL'}")
        return ok and passed
    finally:
        pf.terminate()


def phase4() -> bool:
    global TARGET
    EVIDENCE4.mkdir(parents=True, exist_ok=True)
    print("chart:", flush=True)
    ok = chart_ok()
    ok &= run(sys.executable, "scripts/lint_network.py")
    # App images, rebuilt offline (network: none) so the cluster runs exactly this tree.
    ok &= run("docker", "compose", "build", *APP_SERVICES)
    if not ok:
        return False
    print("deploy (images copied in, nothing pulled):", flush=True)
    if not k8s.up():
        return False
    TARGET = "k8s"
    host_ip = k8s.host_ip()
    ok &= start_probe()
    print("cluster:", flush=True)
    ok &= cluster_state_ok()

    print("offline boundary (NetworkPolicy):", flush=True)
    lm_studio = f"http://{host_ip}:1234/v1/models"
    ok &= egress_blocked(
        ("backend", "mcp-server", "tiles", "frontend"), [*EGRESS_TARGETS[:2], lm_studio]
    )
    # The relay may reach LM Studio and nothing else.
    ok &= egress_blocked(("llm-relay",), EGRESS_TARGETS[:2], from_probe=False)
    ok &= relay_upstream_ok(host_ip)

    ok &= golden_ok()
    ok &= check_mcp()
    print("edge via port-forward:", flush=True)
    ok &= edge_port_forward_ok()

    res = probe("-sS", "-m", "10", "http://llm-relay:1234/v1/models")
    relay = res.returncode == 0 and LLM_MODEL in res.stdout
    print(f"  llm-relay -> LM Studio: {'ok, serving ' + LLM_MODEL if relay else 'FAIL'}")
    if not relay:
        return False
    print("chat (live LLM through the relay; checked against SQL in the cluster DB):", flush=True)
    spec = FENCE_QUESTIONS["q1_dca"]
    out = ask("q1_dca", str(spec["question"]), EVIDENCE4)
    ok &= out is not None and check_fence_answer(spec, out)
    out = ask("q2_track", TRACK_QUESTION["question"], EVIDENCE4)
    ok &= out is not None and check_track_answer(TRACK_QUESTION, out)
    return ok


# --------------------------------------------------------------------------- phase 5

EVIDENCE5 = ROOT / ".verify" / "phase5"
GITEA_URL = (
    f"http://localhost:{os.environ.get('GITEA_PORT') or ci.env_value('GITEA_PORT') or '3000'}"
)
CI_JOBS = {"egress-check", "lint", "typecheck", "test", "web", "helm", "build"}
# app.ini (section, key) -> required value: what keeps Gitea from reaching or serving the internet.
GITEA_SETTINGS = {
    ("security", "install_lock"): "true",
    ("server", "offline_mode"): "true",
    ("server", "disable_ssh"): "true",
    ("service", "disable_registration"): "true",
    ("picture", "disable_gravatar"): "true",
    ("picture", "enable_federated_avatar"): "false",
    ("cron.update_checker", "enabled"): "false",
    ("migrations", "allow_localnetworks"): "false",
    ("mirror", "enabled"): "false",
    ("actions", "enabled"): "true",
    ("actions", "default_actions_url"): "self",
}
RUN_TIMEOUT_S = 45 * 60


def ci_compose(*args: str) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", "ci/docker-compose.yml", *args]
    return subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, check=False)


def gitea_config_ok() -> bool:
    """The effective app.ini (the install page writes it) keeps every offline setting."""
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    app_ini = ci_compose("exec", "-T", "gitea", "cat", "/etc/gitea/app.ini").stdout
    ini.read_string("[_top]\n" + app_ini)  # app.ini starts with section-less keys
    bad = {
        f"{s}.{k}": ini.get(s, k, fallback=None)
        for (s, k), want in GITEA_SETTINGS.items()
        if (ini.get(s, k, fallback="") or "").strip().lower() != want
    }
    print(
        f"  gitea app.ini offline settings ({len(GITEA_SETTINGS)}): {'ok' if not bad else f'FAIL {bad}'}"
    )
    return not bad


def ci_networks_ok() -> bool:
    """Gitea and the runner sit only on the internal network; the edge proxy is the one way in."""
    fmt = "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"
    want = {
        "gitea": {"aperture-ci"},
        "runner": {"aperture-ci"},
        "gitea-edge": {"aperture-ci", "aperture-ci_ci-edge"},
    }
    ok = True
    for svc, nets in want.items():
        cid = ci_compose("ps", "-q", svc).stdout.strip()
        res = subprocess.run(["docker", "inspect", "--format", fmt, cid],
                             capture_output=True, text=True, check=False)  # fmt: skip
        have = set(res.stdout.split()) if cid else set()
        ok &= have == nets
        print(
            f"  {svc} networks: {sorted(have) or 'not running'} {'ok' if have == nets else 'FAIL'}"
        )
    internal = subprocess.run(
        ["docker", "network", "inspect", "aperture-ci", "--format", "{{.Internal}}"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()  # fmt: skip
    print(f"  aperture-ci internal: {internal} {'ok' if internal == 'true' else 'FAIL'}")
    return ok and internal == "true"


def ci_egress_blocked() -> bool:
    ok = True
    for url in EGRESS_TARGETS[:2]:
        res = ci_compose("exec", "-T", "gitea", "curl", "-sS", "-m", "5", "-o", "/dev/null", url)
        blocked = res.returncode != 0
        print(f"  gitea -> {url}: {'blocked' if blocked else 'REACHABLE'}")
        ok &= blocked
    return ok


def gitea_edge_ok() -> bool:
    conf = ci_compose("exec", "-T", "gitea-edge", "nginx", "-T").stdout
    passes = re.findall(r"^\s*\w+_pass\s+([^;]+);", conf, re.MULTILINE)
    targets = {re.sub(r"^\w+://", "", t).split("/")[0] for t in passes}
    resolvers = re.findall(r"^\s*resolver\s", conf, re.MULTILINE)
    ok = bool(conf) and targets == {"gitea:3000"} and not resolvers
    print(
        f"  gitea-edge upstreams: {sorted(targets)}; resolver directives: {len(resolvers)} {'ok' if ok else 'FAIL'}"
    )
    return ok


def git_out(*args: str) -> str:
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    return res.stdout.strip() if res.returncode == 0 else ""


def gitea_api(path: str) -> tuple[int, str]:
    """GET the local Gitea API with the host's curl; a read token in ci/.env (GITEA_READ_TOKEN)
    is sent if present, on stdin so it never appears in a process list."""
    token = ci.env_value("GITEA_READ_TOKEN")
    cmd = [
        "curl",
        "-s",
        "-m",
        "30",
        "-w",
        "\n%{http_code}",
        "-H",
        "@-",
        f"{GITEA_URL}/api/v1{path}",
    ]
    header = f"Authorization: token {token}\n" if token else ""
    res = subprocess.run(cmd, input=header, capture_output=True, text=True, encoding="utf-8",
                         check=False)  # fmt: skip
    body, _, status = res.stdout.rpartition("\n")
    return int(status or 0), body


def push_head() -> tuple[str, str] | None:
    """Push the committed HEAD to the `gitea` remote; returns (owner/repo, sha)."""
    url = git_out("remote", "get-url", "gitea")
    m = re.match(r"^https?://[^/]+/([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        print(f"  no usable `gitea` remote ({url or 'missing'}); see ci/README.md")
        return None
    if dirty := git_out("status", "--porcelain"):
        print(
            f"  working tree has {len(dirty.splitlines())} uncommitted change(s): CI tests commits"
        )
        return None
    sha, branch = git_out("rev-parse", "HEAD"), git_out("branch", "--show-current") or "main"
    if not run("git", "push", "gitea", f"HEAD:refs/heads/{branch}"):
        return None
    return m.group(1), sha


def wait_for_run(repo: str, sha: str) -> dict | None:
    deadline, last = time.monotonic() + RUN_TIMEOUT_S, ""
    while time.monotonic() < deadline:
        status, body = gitea_api(f"/repos/{repo}/actions/runs?head_sha={sha}&event=push")
        if status != 200:
            print(f"  actions API: HTTP {status} {body[:200]!r}")
            if status in (401, 403, 404):
                print("  (a private repo needs a read token in ci/.env as GITEA_READ_TOKEN)")
            return None
        runs = [r for r in json.loads(body).get("workflow_runs") or [] if r.get("head_sha") == sha]
        if runs:
            latest = max(runs, key=lambda r: r["id"])
            state = f"run {latest['id']}: {latest.get('status')}"
            if state != last:
                print(f"  {state}", flush=True)
                last = state
            if latest.get("status") == "completed":
                return latest
        time.sleep(10)
    print(f"  no completed run for {sha[:12]} within {RUN_TIMEOUT_S // 60} min")
    return None


def evidence(what: str, good: bool) -> bool:
    print(f"  {what}: {'ok' if good else 'FAIL'}")
    return good


def run_jobs_ok(repo: str, run_: dict) -> bool:
    status, body = gitea_api(f"/repos/{repo}/actions/runs/{run_['id']}/jobs")
    jobs = (json.loads(body).get("jobs") or []) if status == 200 else []
    logs: dict[str, str] = {}
    for job in jobs:
        code, text = gitea_api(f"/repos/{repo}/actions/jobs/{job['id']}/logs")
        logs[job["name"]] = text if code == 200 else ""
        (EVIDENCE5 / f"{job['name']}.log").write_text(logs[job["name"]], encoding="utf-8")
        print(f"  job {job['name']}: {job.get('conclusion') or job.get('status')}")
    names = {j["name"] for j in jobs}
    ok = evidence(f"jobs {sorted(CI_JOBS)} all ran", names == CI_JOBS)
    ok &= all(j.get("conclusion") == "success" for j in jobs)
    # The offline claims were exercised, not just exited 0.
    egress = logs.get("egress-check", "")
    ok &= evidence(
        "egress-check log: 3 targets blocked, Gitea reachable",
        egress.count(": blocked (") == 3 and "api/healthz: ok" in egress,
    )
    built = re.search(r"built (\d+)/(\d+) image\(s\)", logs.get("build", ""))
    ok &= evidence(
        "build log: every app image built offline",
        built is not None and built[1] == built[2] and built[1] != "0",
    )
    return ok and run_.get("conclusion") == "success"


def phase5() -> bool:
    EVIDENCE5.mkdir(parents=True, exist_ok=True)
    print("local checks (what CI runs):", flush=True)
    ok = run("uv", "run", "ruff", "check", ".")
    ok &= run("uv", "run", "ruff", "format", "--check", ".")
    ok &= run("uv", "run", "mypy")
    ok &= run(sys.executable, "scripts/lint_network.py")
    ok &= run(sys.executable, "bootstrap/build_deps.py", "--check")
    print("CI stack:", flush=True)
    ok &= ci.up()
    if not ci.runner_registered():
        print(
            "  runner not registered: put RUNNER_TOKEN in ci/.env, then `uv run python scripts/ci.py up`"
        )
        return False
    ok &= gitea_config_ok()
    ok &= ci_networks_ok()
    ok &= ci_egress_blocked()
    ok &= gitea_edge_ok()
    if not ok:
        return False
    print("CI run for HEAD:", flush=True)
    pushed = push_head()
    if not pushed:
        return False
    repo, sha = pushed
    run_ = wait_for_run(repo, sha)
    if run_ is None:
        return False
    (EVIDENCE5 / "run.json").write_text(json.dumps(run_, indent=2), encoding="utf-8")
    print(f"  {repo}@{sha[:12]}: {run_.get('conclusion')}  ({run_.get('html_url', '')})")
    return run_jobs_ok(repo, run_)


GATES: dict[str, Callable[..., bool]] = {
    "phase0": phase0,
    "phase1": phase1,
    "phase2": phase2,
    "phase3": phase3,
    "phase4": phase4,
    "phase5": phase5,
}

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)  # progress shows up live when logged to a file
    gate = sys.argv[1] if len(sys.argv) > 1 else ""
    if gate not in GATES:
        sys.exit(__doc__)
    ok = GATES[gate](record=True) if "--record" in sys.argv else GATES[gate]()
    print(f"\n{gate}: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
