"""Phase gate runner.

uv run python scripts/verify.py phase0
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> bool:
    print(f"$ {' '.join(args)}", flush=True)
    return subprocess.run(args, cwd=ROOT, check=False).returncode == 0


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


GATES = {"phase0": phase0}

if __name__ == "__main__":
    gate = sys.argv[1] if len(sys.argv) > 1 else ""
    if gate not in GATES:
        sys.exit(__doc__)
    ok = GATES[gate]()
    print(f"\n{gate}: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
