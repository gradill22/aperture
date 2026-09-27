"""The MCP adapter over the in-memory transport, against the fixtures DB."""

import json
import os

import pytest
from mcp import Client

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server(loaded_db):
    os.environ["DATABASE_URL"] = loaded_db
    from aperture_mcp.server import build_server

    return build_server()


async def test_list_tools(server):
    async with Client(server) as client:
        listed = (await client.list_tools()).tools
    by_name = {t.name: t for t in listed}
    assert set(by_name) == {"search_entities", "get_entity_track", "geofence_alert"}
    schema = by_name["geofence_alert"].input_schema
    assert schema["properties"]["buffer_m"]["maximum"] == 50_000
    assert "description" in schema["properties"]["feature_id"]
    assert "2026-09-24" in by_name["search_entities"].description


async def test_search_then_geofence(server, dca_id):
    async with Client(server) as client:
        found = await client.call_tool("search_entities", {"query": "KDCA", "kind": "feature"})
        results = json.loads(found.content[0].text)["results"]
        assert results[0]["feature_id"] == dca_id

        fenced = await client.call_tool(
            "geofence_alert",
            {"feature_id": dca_id, "buffer_m": 2000,
             "start": "2026-09-24T00:00:00Z", "end": "2026-09-25T00:00:00Z"},
        )  # fmt: skip
    assert not fenced.is_error
    body = json.loads(fenced.content[0].text)
    assert body["n_aircraft"] >= 10 and body["passes"][0]["entry"].endswith("UTC")


async def test_errors_are_tool_errors(server):
    async with Client(server) as client:
        missing = await client.call_tool("get_entity_track", {"identifier": "NOSUCH1"})
        bad = await client.call_tool("geofence_alert", {"feature_id": 1, "buffer_m": -1})
    assert missing.is_error and "NOSUCH1" in missing.content[0].text
    assert bad.is_error
