"""Build every app image with no network, as CI's `build` job does.

    python scripts/ci_build.py [--tag ci]

Build definitions come from docker-compose.yml (`docker compose config`), so this cannot drift
from what the stack runs. Each build is `--network=none --pull=false`: the Dockerfiles are FROM
the prebuilt deps images, and a build that needs anything from outside fails here.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def builds() -> dict[str, dict]:
    cmd = ["docker", "compose", "--profile", "tools", "--profile", "test", "config", "--format", "json"]  # fmt: skip
    config = json.loads(
        subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    )
    return {name: svc["build"] for name, svc in config["services"].items() if "build" in svc}


def main() -> int:
    tag = sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else "ci"
    failed = []
    todo = builds()
    for name, build in todo.items():
        cmd = ["docker", "build", "--network=none", "--pull=false",
               "-f", str(Path(build["context"]) / build.get("dockerfile", "Dockerfile")),
               "-t", f"aperture/{name}:{tag}"]  # fmt: skip
        cmd += [
            a for k, v in (build.get("args") or {}).items() for a in ("--build-arg", f"{k}={v}")
        ]
        if target := build.get("target"):
            cmd += ["--target", target]
        cmd.append(build["context"])
        print(f"$ {' '.join(cmd)}", flush=True)
        if subprocess.run(cmd, cwd=ROOT, check=False).returncode:
            failed.append(name)
    print(
        f"built {len(todo) - len(failed)}/{len(todo)} image(s)"
        + (f"; FAILED: {failed}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
