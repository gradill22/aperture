def test_health_counts_match_fixtures(client, fixtures_meta):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["counts"]["aircraft"] == len(fixtures_meta["aircraft"])
    assert body["counts"]["adsb_position"] == fixtures_meta["positions"]
    assert body["counts"]["osm_feature"] == sum(fixtures_meta["features"].values())
    assert body["time_range"][0].startswith("2026-09-24T00:")


def test_search_airport_by_code_and_name(client, dca_id):
    for q in ("KDCA", "dca", "Reagan National"):
        results = client.get("/entities", params={"q": q, "kind": "feature"}).json()["results"]
        assert results[0]["id"] == dca_id, q
        assert results[0]["icao"] == "KDCA"


def test_search_aircraft_by_hex_registration_callsign(client, pg):
    icao24, reg, callsign = pg.execute(
        "SELECT icao24, registration, callsigns[1] FROM aircraft"
        " WHERE registration IS NOT NULL AND cardinality(callsigns) > 0 ORDER BY icao24 LIMIT 1"
    ).fetchone()
    for q in (icao24, icao24.upper(), reg, reg.lower(), callsign):
        results = client.get("/entities", params={"q": q, "kind": "aircraft"}).json()["results"]
        assert results[0]["icao24"] == icao24, q


def test_search_layer_filter_and_limit(client):
    params = {"q": "heliport", "layer": "airports", "limit": 3}
    results = client.get("/entities", params=params).json()["results"]
    assert 0 < len(results) <= 3
    assert all(r["kind"] == "feature" and r["layer"] == "airports" for r in results)


def test_aircraft_detail_legs_match_flight_view(client, pg):
    icao24 = "a00929"
    body = client.get(f"/entities/aircraft/{icao24}").json()
    (n_legs,) = pg.execute("SELECT count(*) FROM flight WHERE icao24 = %s", (icao24,)).fetchone()
    assert len(body["legs"]) == n_legs
    starts = [leg["start"] for leg in body["legs"]]
    assert starts == sorted(starts)


def test_feature_detail(client, dca_id):
    body = client.get(f"/entities/feature/{dca_id}").json()
    assert body["tags"]["iata"] == "DCA"
    assert body["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert body["area_m2"] > 1_000_000  # DCA is ~3.5 km2


def test_not_found(client):
    assert client.get("/entities/aircraft/zzzzzz").status_code == 404
    assert client.get("/entities/feature/999999999").status_code == 404
    assert client.get("/tracks/zzzzzz").status_code == 404


def test_aircraft_flags_match_registry(client, pg):
    body = client.get("/aircraft/flags").json()
    for flag in ("military", "interesting", "pia", "ladd"):
        (n,) = pg.execute(f"SELECT count(*) FROM aircraft WHERE {flag}").fetchone()
        assert len(body[flag]) == n, flag
        assert len(set(body[flag])) == n
