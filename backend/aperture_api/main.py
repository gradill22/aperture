"""FastAPI app. Run: uvicorn aperture_api.main:app (needs DATABASE_URL)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncConnection

from . import agent, queries
from .db import make_engine

LAYERS = Literal["airports", "ports", "government", "military"]
SEARCH_GROUP = Literal["flights", "airports", "ports", "government", "military"]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = make_engine()
    app.state.dataset = None
    app.state.llm = agent.make_client()
    yield
    await app.state.llm.aclose()
    await app.state.engine.dispose()


app = FastAPI(title="Aperture API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(queries.NotFound)
async def not_found(_: Request, exc: queries.NotFound) -> JSONResponse:
    return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)


@app.exception_handler(queries.BadRequest)
async def bad_request(_: Request, exc: queries.BadRequest) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


async def conn(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.engine.connect() as c:
        yield c


Conn = Annotated[AsyncConnection, Depends(conn)]


async def dataset(request: Request, c: Conn) -> dict:
    """Loaded-dataset metadata, cached once the loader has finished."""
    if request.app.state.dataset is None:
        info = await queries.dataset_info(c)
        if info is None:
            raise HTTPException(503, "database not loaded yet")
        request.app.state.dataset = info
    return request.app.state.dataset


Dataset = Annotated[dict, Depends(dataset)]


def day_bounds(ds: dict) -> tuple[datetime, datetime]:
    t0, t1 = ds["time_range"]
    return datetime.fromisoformat(t0), datetime.fromisoformat(t1)


@app.get("/health")
async def health(ds: Dataset) -> dict:
    return {"status": "ok"} | ds


@app.get("/entities")
async def entities(
    c: Conn,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    kind: Literal["aircraft", "feature"] | None = None,
    layer: LAYERS | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    return {"q": q, "results": await queries.search_entities(c, q, kind, layer, limit)}


@app.get("/search")
async def search(
    c: Conn,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    groups: Annotated[list[SEARCH_GROUP] | None, Query()] = None,
    per_group: Annotated[int, Query(ge=1, le=20)] = 6,
) -> dict:
    """The UI's grouped search (the `/entities` contract is shared with the agent tools)."""
    wanted = [g for g in queries.SEARCH_GROUPS if groups is None or g in groups]
    return {"q": q, "groups": await queries.search_grouped(c, q, wanted, per_group)}


@app.get("/aircraft/flags")
async def aircraft_flags(c: Conn) -> dict:
    return await queries.aircraft_flags(c)


@app.get("/entities/aircraft/{icao24}")
async def aircraft(c: Conn, icao24: str) -> dict:
    return await queries.get_aircraft(c, icao24)


@app.get("/entities/feature/{feature_id}")
async def feature(c: Conn, feature_id: int) -> dict:
    return await queries.get_feature(c, feature_id)


@app.get("/entities/feature/osm/{layer}/{osm_type}/{osm_id}")
async def feature_by_osm(
    c: Conn, layer: LAYERS, osm_type: Literal["node", "way", "relation"], osm_id: int
) -> dict:
    return await queries.get_feature_by_osm(c, layer, osm_type, osm_id)


@app.get("/tracks/{icao24}")
async def tracks(
    c: Conn, icao24: str, start: AwareDatetime | None = None, end: AwareDatetime | None = None
) -> dict:
    return await queries.get_track(c, icao24, start, end)


@app.get("/replay")
async def replay(
    c: Conn,
    t0: AwareDatetime,
    t1: AwareDatetime,
    step: Annotated[int, Query(ge=1, le=300, description="bucket width, seconds")] = 10,
    bbox: Annotated[str | None, Query(description="minlon,minlat,maxlon,maxlat")] = None,
) -> dict:
    box = None
    if bbox:
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(422, "bbox must be minlon,minlat,maxlon,maxlat") from None
        box = (x0, y0, x1, y1)
    return await queries.replay(c, t0, t1, step, box)


class Polygon(BaseModel):
    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list


class GeofenceRequest(BaseModel):
    polygon: Polygon | None = None
    feature_id: int | None = None
    buffer_m: float = Field(0, ge=0, le=50_000)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    include_geometry: bool = True

    @model_validator(mode="after")
    def one_fence(self) -> GeofenceRequest:
        if (self.polygon is None) == (self.feature_id is None):
            raise ValueError("give exactly one of polygon or feature_id")
        return self


@app.post("/geofence")
async def geofence(c: Conn, ds: Dataset, body: GeofenceRequest) -> dict:
    day0, day1 = day_bounds(ds)
    return await queries.geofence(
        c,
        polygon=body.polygon.model_dump() if body.polygon else None,
        feature_id=body.feature_id,
        buffer_m=body.buffer_m,
        start=body.start or day0,
        end=body.end or day1,
        include_geometry=body.include_geometry,
    )


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    tz: Literal["UTC", "ET"] = "UTC"


@app.post("/chat")
async def chat(request: Request, _: Dataset, body: ChatRequest) -> dict:
    try:
        return await agent.chat(
            request.app.state.engine,
            request.app.state.llm,
            [m.model_dump() for m in body.messages],
            body.tz,
        )
    except agent.LLMUnavailable as exc:
        raise HTTPException(502, str(exc)) from None
