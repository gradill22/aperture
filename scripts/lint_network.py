"""Fail if any Python file outside the allowed networked paths imports a network client.

    uv run python scripts/lint_network.py

Networked code is confined to data/snapshot/ (one-time data fetch) and bootstrap/
(image/dependency builds). Later phases add the backend's LM Studio client here.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NETWORK_MODULES = {"httpx", "requests", "urllib", "urllib3", "aiohttp", "socket", "http"}
ALLOWED = ["data/snapshot/", "bootstrap/"]
SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__"}


def network_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        found += [f"{n} (line {node.lineno})" for n in names if n.split(".")[0] in NETWORK_MODULES]
    return found


def main() -> int:
    violations = 0
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if SKIP_DIRS & set(path.relative_to(ROOT).parts) or any(rel.startswith(a) for a in ALLOWED):
            continue
        for imp in network_imports(path):
            print(f"  {rel}: imports {imp}")
            violations += 1
    print("network-import lint: ok" if not violations else f"{violations} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
