"""All SQL for the API (and, from Phase 2, the LLM tools). Returns plain JSON-able dicts.

Times cross this module as aware UTC datetimes and leave it as ISO-8601 'Z' strings or epoch
seconds; coordinates are WGS84 lon/lat rounded to 1e-5 deg (~1 m).
"""

import json
import re
from datetime import UTC, datetime, timedelta

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from . import models

MAX_REPLAY_WINDOW = timedelta(hours=1)
SIMPLIFY_DEG = 0.0002  # ~20 m; for returned leg geometries only, never for hit detection


class NotFound(LookupError):
    pass


class BadRequest(ValueError):
    pass


def iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z") if ts else None


def rnd(x: float | None, nd: int = 5) -> float | None:
    return None if x is None else round(x, nd)


def tokens(q: str) -> list[str]:
    """Search words with LIKE wildcards removed."""
    return [t for t in re.split(r"\s+", re.sub(r"[%_\\]", " ", q).strip()) if t]


# --------------------------------------------------------------------------- dataset


async def dataset_info(conn: AsyncConnection) -> dict | None:
    row = (
        await conn.execute(
            text(
                "SELECT l.loaded_at, l.counts, r.t0, r.t1 FROM load_info l,"
                " (SELECT min(start_ts) AS t0, max(end_ts) AS t1 FROM flight) r"
                " ORDER BY l.loaded_at DESC LIMIT 1"
            )
        )
    ).first()
    if row is None:
        return None
    return {
        "loaded_at": iso(row.loaded_at),
        "counts": row.counts,
        "time_range": [iso(row.t0), iso(row.t1)],
    }


# --------------------------------------------------------------------------- entities

AIRCRAFT_FIELDS = (
    "icao24, registration, type_code, description, owner_operator, year, category,"
    " military, interesting, pia, ladd, n_points, n_points_in_aoi, callsigns"
)
AIRCRAFT_DOC = (
    "concat_ws(' ', icao24, registration, type_code, description, owner_operator,"
    " array_to_string(callsigns, ' '))"
)
FEATURE_DOC = (
    "concat_ws(' ', name, tags->>'alt_name', tags->>'official_name', tags->>'short_name',"
    " tags->>'old_name', tags->>'operator')"
)


def _aircraft_summary(r) -> dict:
    return {
        "kind": "aircraft",
        "id": r.icao24,
        "label": r.registration or r.icao24,
        "icao24": r.icao24,
        "registration": r.registration,
        "type_code": r.type_code,
        "description": r.description,
        "owner_operator": r.owner_operator,
        "year": r.year,
        "category": r.category,
        "military": r.military,
        "interesting": r.interesting,
        "pia": r.pia,
        "ladd": r.ladd,
        "callsigns": list(r.callsigns),
        "n_points": r.n_points,
        "n_points_in_aoi": r.n_points_in_aoi,
    }


def _feature_summary(r) -> dict:
    return {
        "kind": "feature",
        "id": r.id,
        "label": r.name or r.feature_kind,
        "layer": r.layer,
        "feature_kind": r.feature_kind,
        "name": r.name,
        "osm": f"{r.osm_type}/{r.osm_id}",
        "icao": r.icao,
        "iata": r.iata,
        "centroid": [rnd(r.cx), rnd(r.cy)],
    }


async def search_aircraft(conn: AsyncConnection, q: str, limit: int) -> list[dict]:
    words = tokens(q)
    if not words:
        return []
    params: dict = {"q": q.strip().upper(), "limit": limit}
    conds = []
    for i, w in enumerate(words):
        params[f"w{i}"] = f"%{w}%"
        conds.append(f"{AIRCRAFT_DOC} ILIKE :w{i}")
    rows = await conn.execute(
        text(
            f"SELECT {AIRCRAFT_FIELDS},"
            "  CASE WHEN upper(icao24) = :q OR upper(registration) = :q"
            "         OR :q = ANY(callsigns) THEN 0"
            "       WHEN upper(type_code) = :q THEN 1 ELSE 2 END AS rank"
            f" FROM aircraft WHERE {' AND '.join(conds)}"
            " ORDER BY rank, n_points_in_aoi DESC, icao24 LIMIT :limit"
        ),
        params,
    )
    return [_aircraft_summary(r) for r in rows]


FEATURE_SELECT = (
    "SELECT id, layer, kind AS feature_kind, name, osm_type, osm_id,"
    " tags->>'icao' AS icao, tags->>'iata' AS iata,"
    " ST_X(ST_PointOnSurface(geom)) AS cx, ST_Y(ST_PointOnSurface(geom)) AS cy"
)


async def search_features(
    conn: AsyncConnection, q: str, limit: int, layer: str | None = None
) -> list[dict]:
    words = tokens(q)
    if not words:
        return []
    params: dict = {"q": q.strip(), "qu": q.strip().upper(), "limit": limit}
    code_match = "(upper(tags->>'icao') = :qu OR upper(tags->>'iata') = :qu" \
                 " OR upper(tags->>'faa') = :qu OR upper(tags->>'ref') = :qu)"  # fmt: skip
    conds = []
    for i, w in enumerate(words):
        params[f"w{i}"] = f"%{w}%"
        conds.append(f"{FEATURE_DOC} ILIKE :w{i}")
    where = f"({code_match} OR ({' AND '.join(conds)}))"
    if layer:
        where += " AND layer = :layer"
        params["layer"] = layer
    rows = await conn.execute(
        text(
            f"{FEATURE_SELECT} FROM osm_feature WHERE {where}"
            f" ORDER BY {code_match} DESC, (name IS NULL),"
            "  similarity(coalesce(name, ''), :q) DESC, ST_Area(geom) DESC, id LIMIT :limit"
        ),
        params,
    )
    return [_feature_summary(r) for r in rows]


async def search_entities(
    conn: AsyncConnection, q: str, kind: str | None, layer: str | None, limit: int
) -> list[dict]:
    results: list[dict] = []
    if kind in (None, "aircraft") and not layer:
        results += await search_aircraft(conn, q, limit)
    if kind in (None, "feature"):
        results += await search_features(conn, q, limit, layer)
    return results[:limit] if kind else results


async def get_aircraft(conn: AsyncConnection, icao24: str) -> dict:
    a, f = models.aircraft, models.flight
    r = (await conn.execute(select(a).where(a.c.icao24 == icao24.lower()))).first()
    if r is None:
        raise NotFound(f"aircraft {icao24}")
    legs = await conn.execute(
        select(
            f.c.leg, f.c.callsign, f.c.start_ts, f.c.end_ts, f.c.n_points,
            func.ST_XMin(f.c.geom).label("x0"), func.ST_YMin(f.c.geom).label("y0"),
            func.ST_XMax(f.c.geom).label("x1"), func.ST_YMax(f.c.geom).label("y1"),
        ).where(f.c.icao24 == r.icao24).order_by(f.c.leg)
    )  # fmt: skip
    return _aircraft_summary(r) | {
        "legs": [
            {
                "leg": leg.leg,
                "callsign": leg.callsign,
                "start": iso(leg.start_ts),
                "end": iso(leg.end_ts),
                "n_points": leg.n_points,
                "bbox": [rnd(leg.x0), rnd(leg.y0), rnd(leg.x1), rnd(leg.y1)],
            }
            for leg in legs
        ],
    }


FLAGS = ("military", "interesting", "pia", "ladd")


async def aircraft_flags(conn: AsyncConnection) -> dict[str, list[str]]:
    """icao24 lists per registry flag, so the map can style replay rows (which carry no flags)."""
    a = models.aircraft
    rows = await conn.execute(select(a.c.icao24, *(a.c[f] for f in FLAGS)))
    out: dict[str, list[str]] = {f: [] for f in FLAGS}
    for r in rows:
        for f in FLAGS:
            if getattr(r, f):
                out[f].append(r.icao24)
    return out


async def get_feature(conn: AsyncConnection, feature_id: int) -> dict:
    o = models.osm_feature
    surface = func.ST_PointOnSurface(o.c.geom)
    stmt = select(
        o.c.id, o.c.layer, o.c.kind.label("feature_kind"), o.c.name, o.c.osm_type, o.c.osm_id,
        o.c.tags, o.c.tags["icao"].astext.label("icao"), o.c.tags["iata"].astext.label("iata"),
        func.ST_X(surface).label("cx"), func.ST_Y(surface).label("cy"),
        func.ST_AsGeoJSON(o.c.geom, 6).label("geojson"),
        func.ST_XMin(o.c.geom).label("x0"), func.ST_YMin(o.c.geom).label("y0"),
        func.ST_XMax(o.c.geom).label("x1"), func.ST_YMax(o.c.geom).label("y1"),
        func.ST_Area(cast(o.c.geom, Geography)).label("area_m2"),
    ).where(o.c.id == feature_id)  # fmt: skip
    r = (await conn.execute(stmt)).first()
    if r is None:
        raise NotFound(f"feature {feature_id}")
    return _feature_summary(r) | {
        "tags": r.tags,
        "bbox": [rnd(r.x0), rnd(r.y0), rnd(r.x1), rnd(r.y1)],
        "area_m2": round(r.area_m2),
        "geometry": json.loads(r.geojson),
    }


# --------------------------------------------------------------------------- tracks


async def get_track(
    conn: AsyncConnection, icao24: str, start: datetime | None, end: datetime | None
) -> dict:
    """Per-leg LineStrings with parallel timestamp/altitude arrays (full resolution)."""
    a = models.aircraft
    if (await conn.execute(select(a.c.icao24).where(a.c.icao24 == icao24.lower()))).first() is None:
        raise NotFound(f"aircraft {icao24}")
    params: dict = {"icao24": icao24.lower()}
    window = ""
    if start:
        window += " AND p.ts >= :start"
        params["start"] = start
    if end:
        window += " AND p.ts <= :end"
        params["end"] = end
    rows = await conn.execute(
        text(
            "SELECT f.leg, f.callsign AS leg_callsign, p.ts, ST_X(p.geom) AS lon,"
            " ST_Y(p.geom) AS lat, p.alt_baro_ft, p.on_ground, p.gs_kt"
            " FROM flight f JOIN adsb_position p"
            "   ON p.icao24 = f.icao24 AND p.ts BETWEEN f.start_ts AND f.end_ts"
            f" WHERE f.icao24 = :icao24{window}"
            " ORDER BY p.ts"
        ),
        params,
    )
    legs: dict[int, dict] = {}
    for r in rows:
        leg = legs.setdefault(
            r.leg,
            {"leg": r.leg, "callsign": r.leg_callsign, "coordinates": [], "t": [], "alt_ft": []},
        )
        leg["coordinates"].append([rnd(r.lon), rnd(r.lat)])
        leg["t"].append(int(r.ts.timestamp()))
        leg["alt_ft"].append(0 if r.on_ground else r.alt_baro_ft)
    return {
        "icao24": icao24.lower(),
        "start": iso(start),
        "end": iso(end),
        "legs": [
            {
                "leg": leg["leg"],
                "callsign": leg["callsign"],
                "start": iso(datetime.fromtimestamp(leg["t"][0], UTC)),
                "end": iso(datetime.fromtimestamp(leg["t"][-1], UTC)),
                "n_points": len(leg["t"]),
                "geometry": {"type": "LineString", "coordinates": leg["coordinates"]},
                "t": leg["t"],
                "alt_ft": leg["alt_ft"],
            }
            for leg in legs.values()
            if len(leg["t"]) >= 2
        ],
    }


# --------------------------------------------------------------------------- replay

REPLAY_COLUMNS = ["t", "icao24", "lon", "lat", "alt_ft", "track_deg", "gs_kt", "callsign"]


async def replay(
    conn: AsyncConnection,
    t0: datetime,
    t1: datetime,
    step_s: int,
    bbox: tuple[float, float, float, float] | None,
) -> dict:
    """Last known state of every aircraft per time bucket, as compact rows."""
    if t1 <= t0:
        raise BadRequest("t1 must be after t0")
    if t1 - t0 > MAX_REPLAY_WINDOW:
        raise BadRequest(f"window must be <= {MAX_REPLAY_WINDOW}")
    params: dict = {"t0": t0, "t1": t1, "step": timedelta(seconds=step_s)}
    where = "ts >= :t0 AND ts < :t1"
    if bbox:
        where += " AND geom && ST_MakeEnvelope(:x0, :y0, :x1, :y1, 4326)"
        params |= dict(zip(("x0", "y0", "x1", "y1"), bbox, strict=True))
    rows = await conn.execute(
        text(
            "SELECT extract(epoch FROM time_bucket(CAST(:step AS interval), ts,"
            "   CAST(:t0 AS timestamptz)))::bigint AS bucket, icao24,"
            "  last(ST_X(geom), ts) AS lon, last(ST_Y(geom), ts) AS lat,"
            "  last(CASE WHEN on_ground THEN 0 ELSE alt_baro_ft END, ts) AS alt,"
            "  last(track_deg, ts) AS track, last(gs_kt, ts) AS gs, last(callsign, ts) AS cs"
            f" FROM adsb_position WHERE {where}"
            " GROUP BY 1, 2 ORDER BY 1, 2"
        ),
        params,
    )
    data = [
        [r.bucket, r.icao24, rnd(r.lon), rnd(r.lat), r.alt, rnd(r.track, 1), rnd(r.gs, 1), r.cs]
        for r in rows
    ]
    return {
        "t0": iso(t0),
        "t1": iso(t1),
        "step_s": step_s,
        "bbox": list(bbox) if bbox else None,
        "columns": REPLAY_COLUMNS,
        "n_aircraft": len({row[1] for row in data}),
        "rows": data,
    }


# --------------------------------------------------------------------------- geofence


async def fence_geometry(
    conn: AsyncConnection,
    polygon: dict | None,
    feature_id: int | None,
    buffer_m: float,
) -> str:
    """Resolve the fence to a valid (Multi)Polygon GeoJSON string."""
    if (polygon is None) == (feature_id is None):
        raise BadRequest("give exactly one of polygon or feature_id")
    if polygon is not None:
        sql = "SELECT ST_SetSRID(ST_GeomFromGeoJSON(:gj), 4326) AS g"
        params: dict = {"gj": json.dumps(polygon)}
    else:
        sql = "SELECT geom AS g FROM osm_feature WHERE id = :id"
        params = {"id": feature_id}
    r = (
        await conn.execute(
            text(
                f"WITH src AS ({sql}),"
                " fence AS (SELECT CASE WHEN CAST(:buf AS float8) > 0"
                "   THEN ST_Buffer(ST_MakeValid(g)::geography, CAST(:buf AS float8))::geometry"
                "   ELSE ST_CollectionExtract(ST_MakeValid(g), 3) END AS g FROM src)"
                " SELECT ST_AsGeoJSON(g, 6) AS gj, ST_IsEmpty(g) AS empty FROM fence"
            ),
            params | {"buf": float(buffer_m)},
        )
    ).first()
    if r is None:
        raise NotFound(f"feature {feature_id}")
    if r.gj is None or r.empty:
        raise BadRequest("fence has no area (use buffer_m > 0 for point/line features)")
    return r.gj


# Hits are found on the flight LineStringM segments, so a fast aircraft whose consecutive
# reports straddle a small fence still counts. Entry/exit times interpolate M (epoch) at the
# first/last boundary crossing. One hit per flight leg, clipped to [start, end].
GEOFENCE_SQL = """
WITH fence AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:fence), 4326) AS g),
win AS (SELECT extract(epoch FROM CAST(:start AS timestamptz)) AS m0,
               extract(epoch FROM CAST(:end AS timestamptz)) AS m1),
cand AS (
    SELECT f.icao24, f.leg, f.callsign, f.geom
    FROM flight f, fence
    WHERE f.start_ts <= :end AND f.end_ts >= :start AND ST_Intersects(f.geom, fence.g)
),
seg AS (
    SELECT c.icao24, c.leg, c.callsign, (d).geom AS s
    FROM cand c, fence, LATERAL ST_DumpSegments(c.geom) d
    WHERE (d).geom && fence.g
),
part AS (
    SELECT seg.icao24, seg.leg, seg.callsign, seg.s,
           (ST_Dump(ST_Intersection(seg.s, fence.g))).geom AS p
    FROM seg, fence, win
    WHERE ST_M(ST_EndPoint(seg.s)) >= win.m0 AND ST_M(ST_StartPoint(seg.s)) <= win.m1
      AND ST_Intersects(seg.s, fence.g)
),
hit AS (
    SELECT icao24, leg, min(callsign) AS callsign,
           greatest(min(ST_InterpolatePoint(s, CASE WHEN GeometryType(p) = 'POINT'
                                                    THEN p ELSE ST_StartPoint(p) END)),
                    (SELECT m0 FROM win)) AS m_in,
           least(max(ST_InterpolatePoint(s, CASE WHEN GeometryType(p) = 'POINT'
                                                 THEN p ELSE ST_EndPoint(p) END)),
                 (SELECT m1 FROM win)) AS m_out
    FROM part GROUP BY icao24, leg
)
SELECT h.icao24, h.leg, h.callsign, to_timestamp(h.m_in) AS entry_ts,
       to_timestamp(h.m_out) AS exit_ts,
       a.registration, a.type_code, a.description, a.owner_operator, a.military, a.ladd, a.pia,
       inside.n_points, inside.min_alt_ft, inside.max_alt_ft, inside.on_ground,
       ST_AsGeoJSON(ST_Simplify(ST_Force2D(f.geom), CAST(:simplify AS float8), true), 5) AS leg_geojson
FROM hit h
JOIN aircraft a USING (icao24)
JOIN flight f ON f.icao24 = h.icao24 AND f.leg = h.leg
CROSS JOIN fence
LEFT JOIN LATERAL (
    SELECT count(*)::int AS n_points, min(alt_baro_ft) FILTER (WHERE NOT on_ground) AS min_alt_ft,
           max(alt_baro_ft) FILTER (WHERE NOT on_ground) AS max_alt_ft,
           bool_or(on_ground) AS on_ground
    FROM adsb_position p
    WHERE p.icao24 = h.icao24 AND p.ts BETWEEN to_timestamp(h.m_in) AND to_timestamp(h.m_out)
      AND ST_Intersects(p.geom, fence.g)
) inside ON true
WHERE h.m_in <= h.m_out
ORDER BY entry_ts, h.icao24, h.leg
"""


async def geofence(
    conn: AsyncConnection,
    *,
    polygon: dict | None = None,
    feature_id: int | None = None,
    buffer_m: float = 0,
    start: datetime,
    end: datetime,
    include_geometry: bool = True,
) -> dict:
    if end <= start:
        raise BadRequest("end must be after start")
    fence = await fence_geometry(conn, polygon, feature_id, buffer_m)
    rows = await conn.execute(
        text(GEOFENCE_SQL),
        {"fence": fence, "start": start, "end": end, "simplify": SIMPLIFY_DEG},
    )
    hits = []
    for r in rows:
        hit = {
            "icao24": r.icao24,
            "leg": r.leg,
            "callsign": r.callsign,
            "registration": r.registration,
            "type_code": r.type_code,
            "description": r.description,
            "owner_operator": r.owner_operator,
            "military": r.military,
            "ladd": r.ladd,
            "pia": r.pia,
            "entry": iso(r.entry_ts),
            "exit": iso(r.exit_ts),
            "duration_s": round((r.exit_ts - r.entry_ts).total_seconds()),
            "n_points_inside": r.n_points or 0,
            "min_alt_ft": r.min_alt_ft,
            "max_alt_ft": r.max_alt_ft,
            "on_ground": bool(r.on_ground),
        }
        if include_geometry:
            hit["leg_geometry"] = json.loads(r.leg_geojson)
        hits.append(hit)
    return {
        "fence": json.loads(fence),
        "start": iso(start),
        "end": iso(end),
        "n_hits": len(hits),
        "n_aircraft": len({h["icao24"] for h in hits}),
        "hits": hits,
    }
