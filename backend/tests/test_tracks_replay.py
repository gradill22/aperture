from datetime import UTC, datetime

T0 = "2026-09-24T14:00:00Z"
T1 = "2026-09-24T14:15:00Z"


def test_track_full_resolution(client, pg):
    icao24 = "a00929"
    body = client.get(f"/tracks/{icao24}").json()
    (expected,) = pg.execute(
        "SELECT count(*) FROM adsb_position p JOIN flight f"
        " ON p.icao24 = f.icao24 AND p.ts BETWEEN f.start_ts AND f.end_ts"
        " WHERE f.icao24 = %s",
        (icao24,),
    ).fetchone()
    assert sum(leg["n_points"] for leg in body["legs"]) == expected
    for leg in body["legs"]:
        assert len(leg["t"]) == len(leg["alt_ft"]) == len(leg["geometry"]["coordinates"])
        assert leg["t"] == sorted(leg["t"])


def test_track_window(client):
    body = client.get("/tracks/a00929", params={"start": T0, "end": T1}).json()
    lo = datetime.fromisoformat(T0).timestamp()
    hi = datetime.fromisoformat(T1).timestamp()
    assert body["legs"]
    assert all(lo <= t <= hi for leg in body["legs"] for t in leg["t"])


def test_replay_rows_are_last_position_per_bucket(client, pg):
    step = 30
    body = client.get("/replay", params={"t0": T0, "t1": T1, "step": step}).json()
    cols = body["columns"]
    rows = [dict(zip(cols, r, strict=True)) for r in body["rows"]]
    assert rows
    lo = int(datetime.fromisoformat(T0).timestamp())
    keys = [(r["t"], r["icao24"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert all((r["t"] - lo) % step == 0 and lo <= r["t"] < lo + 900 for r in rows)
    # Ground truth for a sample row: the latest report inside its bucket.
    sample = rows[len(rows) // 2]
    b0 = datetime.fromtimestamp(sample["t"], UTC)
    lon, lat = pg.execute(
        "SELECT ST_X(geom), ST_Y(geom) FROM adsb_position WHERE icao24 = %s"
        " AND ts >= %s AND ts < %s + interval '30 seconds' ORDER BY ts DESC LIMIT 1",
        (sample["icao24"], b0, b0),
    ).fetchone()
    assert (sample["lon"], sample["lat"]) == (round(lon, 5), round(lat, 5))


def test_replay_bbox_and_limits(client):
    bbox = "-77.2,38.7,-76.8,39.0"
    body = client.get("/replay", params={"t0": T0, "t1": T1, "bbox": bbox}).json()
    assert body["rows"]
    assert all(-77.2 <= r[2] <= -76.8 and 38.7 <= r[3] <= 39.0 for r in body["rows"])
    too_long = {"t0": T0, "t1": "2026-09-24T15:00:01Z"}
    assert client.get("/replay", params=too_long).status_code == 400
    assert client.get("/replay", params={"t0": T1, "t1": T0}).status_code == 400
    assert client.get("/replay", params={"t0": T0, "t1": T1, "bbox": "1,2"}).status_code == 422
