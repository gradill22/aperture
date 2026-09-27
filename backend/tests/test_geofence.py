from datetime import datetime


def test_dca_2km_hand_verified(client, pg, dca_id, fixtures_meta):
    """make_fixtures.py chose its first 10 aircraft independently (haversine <= 2 km from the
    DCA centroid, which lies inside the aerodrome polygon), so all must hit a 2 km buffer."""
    body = client.post(
        "/geofence", json={"feature_id": dca_id, "buffer_m": 2000, "include_geometry": False}
    ).json()
    hit_icaos = {h["icao24"] for h in body["hits"]}
    assert set(fixtures_meta["aircraft"][:10]) <= hit_icaos

    # PostGIS geodesic ground truth: any report within 2000 m of the polygon must be a hit ...
    truth = {
        r[0]
        for r in pg.execute(
            "SELECT DISTINCT p.icao24 FROM adsb_position p, osm_feature f WHERE f.id = %s"
            " AND ST_DWithin(p.geom::geography, f.geom::geography, 2000)",
            (dca_id,),
        )
    }
    assert truth <= hit_icaos
    # ... and every hit leg must actually pass within the buffer (+ buffer polygon tolerance).
    for h in body["hits"]:
        (near,) = pg.execute(
            "SELECT ST_DWithin(fl.geom::geography, f.geom::geography, 2050)"
            " FROM flight fl, osm_feature f WHERE fl.icao24 = %s AND fl.leg = %s AND f.id = %s",
            (h["icao24"], h["leg"], dca_id),
        ).fetchone()
        assert near, h
        assert h["entry"] <= h["exit"]


def test_polygon_fence_with_time_window(client, dca_id):
    box = [[-77.05, 38.84], [-77.03, 38.84], [-77.03, 38.86], [-77.05, 38.86], [-77.05, 38.84]]
    start, end = "2026-09-24T12:00:00Z", "2026-09-24T15:00:00Z"
    body = client.post(
        "/geofence",
        json={"polygon": {"type": "Polygon", "coordinates": [box]}, "start": start, "end": end},
    ).json()
    assert body["n_hits"] > 0
    lo, hi = datetime.fromisoformat(start), datetime.fromisoformat(end)
    for h in body["hits"]:
        assert lo <= datetime.fromisoformat(h["entry"]) <= datetime.fromisoformat(h["exit"]) <= hi
        assert h["leg_geometry"]["type"] == "LineString"
    whole_day = client.post("/geofence", json={"feature_id": dca_id, "buffer_m": 2000}).json()
    assert {h["icao24"] for h in body["hits"]} <= {h["icao24"] for h in whole_day["hits"]}


def test_validation(client, pg, dca_id):
    box = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert client.post("/geofence", json={}).status_code == 422
    both = {"polygon": box, "feature_id": dca_id}
    assert client.post("/geofence", json=both).status_code == 422
    (node_id,) = pg.execute("SELECT id FROM osm_feature WHERE osm_type = 'node' LIMIT 1").fetchone()
    assert client.post("/geofence", json={"feature_id": node_id}).status_code == 400
    buffered = {"feature_id": node_id, "buffer_m": 500}
    assert client.post("/geofence", json=buffered).status_code == 200
    assert client.post("/geofence", json={"feature_id": 999999999}).status_code == 404
