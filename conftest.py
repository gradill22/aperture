"""Shared DB fixtures. Tests run against the fixtures DB: docker compose --profile test up -d db-test"""

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "data" / "fixtures"
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://aperture:aperture@127.0.0.1:55432/aperture"
)


@pytest.fixture(scope="session")
def fixtures_meta() -> dict:
    return json.loads((FIXTURES / "fixtures.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def loaded_db() -> str:
    """Load data/fixtures (skipped by the loader when already loaded)."""
    subprocess.run(
        [sys.executable, "-m", "aperture_loader.load", "--adsb-dir", str(FIXTURES),
         "--osm-dir", str(FIXTURES), "--database-url", TEST_DB],
        check=True,
    )  # fmt: skip
    return TEST_DB


@pytest.fixture(scope="session")
def pg(loaded_db: str):
    """Plain psycopg connection for ground-truth queries, independent of the API code."""
    with psycopg.connect(loaded_db, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def dca_id(pg) -> int:
    return pg.execute("SELECT id FROM osm_feature WHERE tags->>'icao' = 'KDCA'").fetchone()[0]
