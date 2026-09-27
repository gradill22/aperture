"""Tool registry shared by the in-process agent loop (agent.py) and the MCP adapter (mcp-server/).

Each tool takes a validated pydantic input and returns a ToolResult: a compact JSON-able dict for
the LLM (small context window) plus map actions for the frontend (full geometry).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from . import queries

DATASET = (
    "Dataset: one day of ADS-B tracks (2026-09-24, 00:00-24:00 UTC) for every aircraft that flew "
    "within 50 miles of Washington DC, plus OpenStreetMap airports/heliports, ports/marinas, "
    "government and military sites in that area."
)
TIME_ARG = (
    "ISO-8601 time on 2026-09-24. Include an offset (e.g. 2026-09-24T14:00:00Z); a time without "
    "one is read in the analyst's display timezone."
)
TIMEZONES = {"UTC": ZoneInfo("UTC"), "ET": ZoneInfo("America/New_York")}
MAX_LIST = 25


@dataclass
class ToolContext:
    tz: Literal["UTC", "ET"] = "UTC"

    @property
    def zone(self) -> ZoneInfo:
        return TIMEZONES[self.tz]

    def when(self, value: str | datetime | None) -> datetime | None:
        """Parse a tool time argument; naive times are in the display timezone."""
        if value is None or value == "":
            return None
        ts = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return ts.replace(tzinfo=self.zone) if ts.tzinfo is None else ts

    def fmt(self, iso: str | None) -> str | None:
        """ISO 'Z' string -> 'YYYY-MM-DD HH:MM:SS <zone>' in the display timezone."""
        if iso is None:
            return None
        return datetime.fromisoformat(iso).astimezone(self.zone).strftime("%Y-%m-%d %H:%M:%S %Z")


@dataclass
class ToolResult:
    data: dict[str, Any]
    map_actions: list[dict[str, Any]] = field(default_factory=list)


class ToolInput(BaseModel):
    # Small local models add stray keys and send "" for "unset"; tolerate both.
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def blank_is_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: (None if v == "" else v) for k, v in data.items()}
        return data


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[ToolInput]
    fn: Callable[[AsyncConnection, Any, ToolContext], Awaitable[ToolResult]]

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema


def _bbox_of(coords: list[list[float]]) -> list[float]:
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    return [min(xs), min(ys), max(xs), max(ys)]


# --------------------------------------------------------------------------- search_entities


class SearchInput(ToolInput):
    query: str = Field(
        min_length=1,
        description="Free text: aircraft icao24 hex, registration (tail number), callsign, type or "
        "operator; or a place name or ICAO/IATA/FAA code (e.g. 'KDCA', 'Andrews', 'Pentagon').",
    )
    kind: Literal["aircraft", "feature"] | None = Field(
        None, description="Restrict to aircraft or to infrastructure features."
    )
    layer: Literal["airports", "ports", "government", "military"] | None = Field(
        None, description="Restrict features to one layer."
    )
    limit: int = Field(5, ge=1, le=20)


async def search_entities(conn: AsyncConnection, args: SearchInput, _: ToolContext) -> ToolResult:
    results = await queries.search_entities(conn, args.query, args.kind, args.layer, args.limit)
    compact = []
    for r in results:
        if r["kind"] == "aircraft":
            compact.append({
                "kind": "aircraft", "icao24": r["icao24"], "registration": r["registration"],
                "type": r["type_code"], "description": r["description"],
                "operator": r["owner_operator"], "callsigns": r["callsigns"][:5],
                "military": r["military"], "ladd": r["ladd"], "pia": r["pia"],
            })  # fmt: skip
        else:
            compact.append({
                "kind": "feature", "feature_id": r["id"], "name": r["name"], "layer": r["layer"],
                "tag": r["feature_kind"], "icao": r["icao"], "iata": r["iata"],
            })  # fmt: skip
    features = [r for r in results if r["kind"] == "feature"]
    actions = []
    if features:
        actions.append({
            "type": "highlight_features",
            "features": [{k: f[k] for k in ("id", "layer", "name", "centroid")} for f in features],
        })  # fmt: skip
    return ToolResult({"query": args.query, "n_results": len(compact), "results": compact}, actions)


# --------------------------------------------------------------------------- get_entity_track


class TrackInput(ToolInput):
    identifier: str = Field(
        min_length=1, description="Aircraft icao24 hex, registration, or callsign."
    )
    start: str | None = Field(None, description=f"Window start. {TIME_ARG}")
    end: str | None = Field(None, description=f"Window end. {TIME_ARG}")


async def resolve_aircraft(conn: AsyncConnection, identifier: str) -> str | None:
    """icao24 for a hex, registration or callsign (most-seen aircraft wins on ties)."""
    row = (
        await conn.execute(
            text(
                "SELECT icao24 FROM aircraft WHERE icao24 = lower(:q) OR upper(registration) = :u"
                " OR :u = ANY(callsigns) ORDER BY (icao24 = lower(:q)) DESC,"
                " (upper(registration) = :u) DESC, n_points_in_aoi DESC LIMIT 1"
            ),
            {"q": identifier.strip(), "u": identifier.strip().upper()},
        )
    ).first()
    return row.icao24 if row else None


NEAREST_AIRPORT = text(
    "SELECT concat_ws(' ', coalesce(tags->>'icao', tags->>'faa'), name) AS label FROM osm_feature"
    " WHERE layer = 'airports' AND kind = 'aeroway=aerodrome'"
    "   AND ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:x, :y), 4326)::geography, 5000)"
    " ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:x, :y), 4326) LIMIT 1"
)


async def describe_end(conn: AsyncConnection, lon: float, lat: float, alt_ft: int | None) -> str:
    """Where a leg starts/ends, stated only as far as the data supports it."""
    row = (await conn.execute(NEAREST_AIRPORT, {"x": lon, "y": lat})).first()
    where = (
        f"near {row.label}" if row else f"at {lon:.3f},{lat:.3f} (no mapped airport within 5 km)"
    )
    height = "on the ground" if alt_ft == 0 else f"{alt_ft} ft" if alt_ft is not None else "alt n/a"
    return f"{where}, {height}"


async def get_entity_track(conn: AsyncConnection, args: TrackInput, ctx: ToolContext) -> ToolResult:
    icao24 = await resolve_aircraft(conn, args.identifier)
    if icao24 is None:
        raise queries.NotFound(f"no aircraft matches {args.identifier!r}; try search_entities")
    info = await queries.get_aircraft(conn, icao24)
    track = await queries.get_track(conn, icao24, ctx.when(args.start), ctx.when(args.end))
    legs = track["legs"]
    data = {
        "icao24": icao24,
        "registration": info["registration"],
        "type": info["type_code"],
        "description": info["description"],
        "operator": info["owner_operator"],
        "military": info["military"],
        "window": [ctx.fmt(track["start"]), ctx.fmt(track["end"])],
        "n_legs": len(legs),
        "note": "Airports are only known inside the 50-mile area; a leg that starts or ends "
        "airborne entered or left receiver coverage there.",
        "legs": [
            {
                "leg": leg["leg"],
                "callsign": leg["callsign"],
                "start": ctx.fmt(leg["start"]),
                "end": ctx.fmt(leg["end"]),
                "n_points": leg["n_points"],
                "max_alt_ft": max((a for a in leg["alt_ft"] if a is not None), default=None),
                "first_seen": await describe_end(
                    conn, coords[0][0], coords[0][1], leg["alt_ft"][0]
                ),
                "last_seen": await describe_end(
                    conn, coords[-1][0], coords[-1][1], leg["alt_ft"][-1]
                ),
            }
            for leg in legs
            for coords in [leg["geometry"]["coordinates"]]
        ],
    }
    actions = []
    if legs:
        coords = [c for leg in legs for c in leg["geometry"]["coordinates"]]
        actions = [
            {"type": "show_track", "icao24": icao24, "label": info["label"], "legs": legs},
            {"type": "fit_bounds", "bbox": _bbox_of(coords)},
        ]
    return ToolResult(data, actions)


# --------------------------------------------------------------------------- geofence_alert


class PolygonGeometry(BaseModel):
    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list


class GeofenceInput(ToolInput):
    feature_id: int | None = Field(
        None, description="Fence around this feature (feature_id from search_entities)."
    )
    polygon: PolygonGeometry | None = Field(
        None, description="Or a GeoJSON Polygon/MultiPolygon fence in lon/lat."
    )
    buffer_m: float = Field(
        0, ge=0, le=50_000, description="Grow the fence by this many meters (e.g. 2000 for 2 km)."
    )
    start: str | None = Field(None, description=f"Window start (default: whole day). {TIME_ARG}")
    end: str | None = Field(None, description=f"Window end (default: whole day). {TIME_ARG}")


async def geofence_alert(
    conn: AsyncConnection, args: GeofenceInput, ctx: ToolContext
) -> ToolResult:
    day = await queries.dataset_info(conn)
    if day is None:
        raise queries.BadRequest("database not loaded yet")
    start = ctx.when(args.start) or datetime.fromisoformat(day["time_range"][0])
    end = ctx.when(args.end) or datetime.fromisoformat(day["time_range"][1])
    fence_name = None
    if args.feature_id is not None:
        feature = await queries.get_feature(conn, args.feature_id)
        fence_name = feature["name"] or feature["feature_kind"]
    result = await queries.geofence(
        conn,
        polygon=args.polygon.model_dump() if args.polygon else None,
        feature_id=args.feature_id,
        buffer_m=args.buffer_m,
        start=start,
        end=end,
    )
    hits = result["hits"]
    data = {
        "fence": fence_name or "drawn polygon",
        "buffer_m": args.buffer_m,
        "window": [ctx.fmt(result["start"]), ctx.fmt(result["end"])],
        "n_aircraft": result["n_aircraft"],
        "n_passes": result["n_hits"],
        "passes": [
            {
                "icao24": h["icao24"], "callsign": h["callsign"], "registration": h["registration"],
                "type": h["type_code"], "operator": h["owner_operator"],
                "military": h["military"], "entry": ctx.fmt(h["entry"]),
                "exit": ctx.fmt(h["exit"]), "min_alt_ft": h["min_alt_ft"],
                "max_alt_ft": h["max_alt_ft"], "touched_ground_inside": h["on_ground"],
            }
            for h in hits[:MAX_LIST]
        ],
    }  # fmt: skip
    if len(hits) > MAX_LIST:
        data["note"] = f"showing first {MAX_LIST} of {len(hits)} passes by entry time"
    fence = result["fence"]
    rings = fence["coordinates"] if fence["type"] == "Polygon" else fence["coordinates"][0]
    actions = [
        {"type": "show_geofence_hits", "fence": fence, "start": result["start"],
         "end": result["end"], "hits": hits},
        {"type": "fit_bounds", "bbox": _bbox_of(rings[0])},
    ]  # fmt: skip
    return ToolResult(data, actions)


# --------------------------------------------------------------------------- registry

REGISTRY: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "search_entities",
            f"Find aircraft or infrastructure features by name, code or identifier. Use it first "
            f"to turn a place name into a feature_id or a tail number into an icao24. {DATASET}",
            SearchInput,
            search_entities,
        ),
        Tool(
            "get_entity_track",
            f"Flight track of one aircraft: its flight legs with times, altitudes and first/last "
            f"positions, and draws the track on the map. {DATASET}",
            TrackInput,
            get_entity_track,
        ),
        Tool(
            "geofence_alert",
            f"Which aircraft entered a fence (a feature from search_entities plus an optional "
            f"buffer in meters, or a drawn polygon) and when they entered/left it, within an "
            f"optional time window. Draws the fence and the matching tracks. {DATASET}",
            GeofenceInput,
            geofence_alert,
        ),
    )
}


def openai_tools() -> list[dict[str, Any]]:
    """The registry in OpenAI chat-completions `tools` format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.json_schema(),
            },
        }
        for t in REGISTRY.values()
    ]


async def call_tool(
    conn: AsyncConnection, name: str, arguments: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    """Validate and run one tool. Raises KeyError, pydantic.ValidationError, NotFound, BadRequest."""
    tool = REGISTRY[name]
    return await tool.fn(conn, tool.input_model.model_validate(arguments), ctx)
