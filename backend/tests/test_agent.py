"""/chat tool loop against a respx-mocked OpenAI endpoint (no LLM needed)."""

import json

import httpx
import pytest
import respx
from aperture_api import agent

COMPLETIONS = f"{agent.LLM_BASE_URL}/chat/completions"


def tool_call(call_id: str, name: str, args: dict) -> httpx.Response:
    """An LM Studio-shaped tool-call turn (arguments as a JSON string, reasoning split out)."""
    message = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "thinking about which tool to use",
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}],
    }  # fmt: skip
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "tool_calls"}]})


def answer(text: str) -> httpx.Response:
    message = {"role": "assistant", "content": text}
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})


def last_tool_result(request: httpx.Request) -> dict:
    messages = json.loads(request.content)["messages"]
    return json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"])


def ask(client, question: str, tz: str = "UTC"):
    return client.post("/chat", json={"messages": [{"role": "user", "content": question}], "tz": tz})


def test_search_then_geofence(client, dca_id):
    requests: list[dict] = []

    def llm(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        match len(requests):
            case 1:
                return tool_call("c1", "search_entities", {"query": "Reagan National", "kind": "feature"})
            case 2:
                feature_id = last_tool_result(request)["results"][0]["feature_id"]
                return tool_call("c2", "geofence_alert", {
                    "feature_id": feature_id, "buffer_m": 2000,
                    "start": "2026-09-24T14:00:00Z", "end": "2026-09-24T15:00:00Z",
                })  # fmt: skip
            case _:
                passes = last_tool_result(request)["passes"]
                return answer(f"<think>count them</think>{len(passes)} passes: "
                              + ", ".join(p["icao24"] for p in passes))  # fmt: skip

    with respx.mock(assert_all_called=True) as mock:
        mock.post(COMPLETIONS).mock(side_effect=llm)
        resp = ask(client, "Which aircraft came within 2 km of Reagan National 14-15 UTC?")
    assert resp.status_code == 200, resp.text
    out = resp.json()

    assert [c["name"] for c in out["tool_calls"]] == ["search_entities", "geofence_alert"]
    assert all(c["ok"] for c in out["tool_calls"])
    assert out["tool_calls"][1]["arguments"]["feature_id"] == dca_id
    assert "<think>" not in out["reply"] and out["reply"].split()[1] == "passes:"
    assert [a["type"] for a in out["map_actions"]] == [
        "highlight_features", "show_geofence_hits", "fit_bounds"
    ]  # fmt: skip
    hits = out["map_actions"][1]["hits"]
    assert hits and all("leg_geometry" in h for h in hits)
    assert {h["icao24"] for h in hits} <= set(out["reply"].replace(",", " ").split())

    # Request shape: tools offered, display tz in the system prompt, reasoning never echoed back.
    assert {t["function"]["name"] for t in requests[0]["tools"]} == {
        "search_entities", "get_entity_track", "geofence_alert"
    }  # fmt: skip
    assert "UTC" in requests[0]["messages"][0]["content"]
    replay = requests[2]["messages"]
    assert [m["role"] for m in replay] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert all("reasoning_content" not in m for m in replay)
    assert replay[3]["tool_call_id"] == "c1" and replay[5]["tool_call_id"] == "c2"


def test_tool_errors_go_back_to_the_model(client):
    seen: list[dict] = []

    def llm(request: httpx.Request) -> httpx.Response:
        if len(seen) == 0:
            seen.append({})
            return tool_call("c1", "get_entity_track", {"identifier": "NOSUCH1"})
        if len(seen) == 1:
            seen.append(last_tool_result(request))
            return tool_call("c2", "geofence_alert", {"buffer_m": -5})
        seen.append(last_tool_result(request))
        return answer("I could not find that aircraft.")

    with respx.mock() as mock:
        mock.post(COMPLETIONS).mock(side_effect=llm)
        out = ask(client, "Track NOSUCH1").json()
    assert "NOSUCH1" in seen[1]["error"]
    assert seen[2]["error"] == "invalid arguments"
    assert [c["ok"] for c in out["tool_calls"]] == [False, False]
    assert out["map_actions"] == [] and out["reply"] == "I could not find that aircraft."


def test_track_in_eastern_time(client, fixtures_meta):
    icao24 = fixtures_meta["aircraft"][0]
    calls = iter([tool_call("c1", "get_entity_track", {"identifier": icao24}), None])

    def llm(request: httpx.Request) -> httpx.Response:
        nxt = next(calls)
        return nxt or answer(json.dumps(last_tool_result(request)))

    with respx.mock() as mock:
        mock.post(COMPLETIONS).mock(side_effect=llm)
        out = ask(client, f"Where did {icao24} fly?", tz="ET").json()
    result = json.loads(out["reply"])
    assert result["icao24"] == icao24 and result["n_legs"] >= 1
    assert all(leg["start"].endswith(("EDT", "EST")) for leg in result["legs"])
    assert all(leg["first_seen"] and leg["last_seen"] for leg in result["legs"])
    assert [a["type"] for a in out["map_actions"]] == ["show_track", "fit_bounds"]


def test_step_cap_forces_an_answer(client):
    bodies: list[dict] = []

    def llm(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if bodies[-1]["tool_choice"] == "none":
            return answer("done")
        return tool_call(f"c{len(bodies)}", "search_entities", {"query": "Andrews"})

    with respx.mock() as mock:
        mock.post(COMPLETIONS).mock(side_effect=llm)
        out = ask(client, "loop forever").json()
    assert out["reply"] == "done"
    assert len(out["tool_calls"]) == agent.MAX_STEPS
    assert len(bodies) == agent.MAX_STEPS + 1


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.Response(500, text="boom")])
def test_llm_unavailable_is_502(client, failure):
    with respx.mock() as mock:
        route = mock.post(COMPLETIONS)
        if isinstance(failure, Exception):
            route.mock(side_effect=failure)
        else:
            route.mock(return_value=failure)
        resp = ask(client, "hello")
    assert resp.status_code == 502
