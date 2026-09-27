"""Backend fixtures; shared DB fixtures live in the repo-root conftest.py."""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client(loaded_db: str):
    os.environ["DATABASE_URL"] = loaded_db
    from aperture_api.main import app

    with TestClient(app) as c:
        yield c
