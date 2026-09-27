"""Offline check that every snapshot output matches MANIFEST.json (sha256 + size).

uv run python data/snapshot/verify.py
"""

import json
import sys

from common import DATA_DIR, MANIFEST_PATH, sha256_file


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text())
    failures = 0
    for section, entry in manifest.items():
        for name, out in entry.get("outputs", {}).items():
            path = DATA_DIR / out["path"]
            if not path.exists():
                status = "MISSING"
            elif path.stat().st_size != out["bytes"] or sha256_file(path) != out["sha256"]:
                status = "MISMATCH"
            else:
                status = "ok"
            failures += status != "ok"
            counts = {k: out[k] for k in ("rows", "features") if k in out}
            print(
                f"  {status:8} {section}/{name}: {out['path']} ({out['bytes'] / 1e6:.1f} MB) {counts or ''}"
            )
    print("manifest verified" if not failures else f"{failures} output(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
