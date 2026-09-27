"""Prepare the CI test database (a bare `services:` container) the way initdb does elsewhere.

    python scripts/ci_db_init.py        (uses TEST_DATABASE_URL)

Compose and Helm mount db/init/*.sql into /docker-entrypoint-initdb.d and pass
`-c timescaledb.telemetry_level=off`; a CI service container gets neither, so this waits for the
server, turns telemetry off and applies the same SQL files in order.
"""

import os
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def connect(url: str, timeout_s: float = 120) -> psycopg.Connection:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return psycopg.connect(url, autocommit=True, connect_timeout=5)
        except psycopg.OperationalError as e:
            if time.monotonic() > deadline:
                raise
            print(f"  waiting for the database: {str(e).strip().splitlines()[0]}", flush=True)
            time.sleep(2)


def main() -> int:
    url = os.environ["TEST_DATABASE_URL"]
    with connect(url) as conn:
        conn.execute("ALTER SYSTEM SET timescaledb.telemetry_level = 'off'")
        conn.execute("SELECT pg_reload_conf()")
        for sql in sorted((ROOT / "db" / "init").glob("*.sql")):
            conn.execute(sql.read_text(encoding="utf-8"))
            print(f"  applied {sql.relative_to(ROOT).as_posix()}")
        level = conn.execute("SHOW timescaledb.telemetry_level").fetchone()
        print(f"  timescaledb.telemetry_level = {level[0] if level else '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
