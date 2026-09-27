"""Tool-calling loop against the local LLM (LM Studio, OpenAI-compatible chat completions).

This is the only backend module that makes network calls, and only to LLM_BASE_URL (in compose,
the llm-relay container that forwards to LM Studio on the host).
"""

import json
import os
import re
from typing import Any, Literal

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from . import queries, tools

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3.5-9b")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "180"))
MAX_STEPS = 6

THINK = re.compile(r"<think>.*?</think>|^.*?</think>", re.DOTALL)

SYSTEM = """You are Aperture, an OSINT analyst assistant working fully offline.
{dataset}
The analyst's display timezone is {tz_name}. Times the analyst gives without a zone are in {tz_name}; \
pass them to tools with an explicit offset. Report times in {tz_name}.
Always answer from tool results, never from memory. To ask about a place, first call \
search_entities to get its feature_id, then call geofence_alert. Distances such as "within 2 km" \
are the buffer_m of the fence (2000). Name aircraft by icao24 plus registration or callsign; when \
there are more than 10, give the total and list the first 10. Only state facts that are in the \
tool results; never guess airports, origins or destinations from coordinates. Keep the answer \
short: the map already shows tracks and fences."""

TZ_NAMES = {"UTC": "UTC", "ET": "US Eastern Time (America/New_York)"}


class LLMUnavailable(RuntimeError):
    """LM Studio could not be reached or returned an unusable response."""


def strip_reasoning(text: str | None) -> str:
    return THINK.sub("", text or "").strip()


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT_S)


async def _complete(client: httpx.AsyncClient, messages: list[dict], **extra: Any) -> dict:
    body = {"model": LLM_MODEL, "messages": messages, "temperature": 0} | extra
    try:
        resp = await client.post("/chat/completions", json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]
    except httpx.HTTPStatusError as exc:
        raise LLMUnavailable(f"LLM returned HTTP {exc.response.status_code}: "
                             f"{exc.response.text[:300]}") from exc  # fmt: skip
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise LLMUnavailable(f"LLM unreachable at {LLM_BASE_URL}: {exc!r}") from exc


async def _run_tool(engine: AsyncEngine, call: dict, ctx: tools.ToolContext) -> tuple[dict, dict]:
    """Execute one tool call. Returns (payload for the LLM, trace entry). Errors go to the LLM."""
    fn = call.get("function") or {}
    name, raw = fn.get("name", ""), fn.get("arguments") or "{}"
    trace: dict[str, Any] = {"name": name, "arguments": raw}
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
        trace["arguments"] = args
        if name not in tools.REGISTRY:
            raise LookupError(f"unknown tool {name!r}; available: {', '.join(tools.REGISTRY)}")
        async with engine.connect() as conn:
            result = await tools.call_tool(conn, name, args, ctx)
    except ValidationError as exc:
        errors = [
            {"field": ".".join(map(str, e["loc"])), "problem": e["msg"]} for e in exc.errors()
        ]
        payload = {"error": "invalid arguments", "details": errors}
    except (json.JSONDecodeError, LookupError, queries.BadRequest, DBAPIError) as exc:
        detail = str(exc.orig) if isinstance(exc, DBAPIError) else str(exc)
        payload = {"error": detail.splitlines()[0] if detail else type(exc).__name__}
    else:
        trace |= {"ok": True, "map_actions": [a["type"] for a in result.map_actions]}
        return {"result": result.data, "actions": result.map_actions}, trace
    trace |= {"ok": False, "error": payload["error"]}
    return {"result": payload, "actions": []}, trace


async def chat(
    engine: AsyncEngine,
    client: httpx.AsyncClient,
    history: list[dict],
    tz: Literal["UTC", "ET"] = "UTC",
) -> dict:
    """Run the tool loop for one analyst turn.

    history: [{role: user|assistant, content}] (earlier turns, text only). Returns
    {reply, map_actions, tool_calls} where tool_calls is a trace for the UI/tests.
    """
    ctx = tools.ToolContext(tz)
    system = SYSTEM.format(dataset=tools.DATASET, tz_name=TZ_NAMES[tz])
    messages = [{"role": "system", "content": system}] + [
        {"role": m["role"], "content": m["content"]} for m in history
    ]
    offered = tools.openai_tools()
    map_actions: list[dict] = []
    trace: list[dict] = []

    for step in range(MAX_STEPS + 1):
        # Last round: tools withheld so the model must answer with what it has.
        final = step == MAX_STEPS
        msg = await _complete(
            client, messages, tools=offered, tool_choice="none" if final else "auto"
        )
        calls = [] if final else (msg.get("tool_calls") or [])
        content = strip_reasoning(msg.get("content"))
        if not calls:
            return {"reply": content, "map_actions": map_actions, "tool_calls": trace}
        # Echo the call back without any reasoning_content.
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for i, call in enumerate(calls):
            payload, entry = await _run_tool(engine, call, ctx)
            map_actions += payload["actions"]
            trace.append(entry)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id") or f"call_{step}_{i}",
                "content": json.dumps(payload["result"], separators=(",", ":"), default=str),
            })  # fmt: skip
    raise AssertionError("unreachable")
