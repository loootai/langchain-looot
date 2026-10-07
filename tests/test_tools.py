import asyncio
import json

import httpx
import pytest
from langchain_core.tools import BaseTool

from langchain_looot import LoootClient, LoootError, LoootToolkit


def make(handler, **kw):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = LoootClient("test-token", base_url="https://api.looot.ai", transport=httpx.MockTransport(wrapped))
    tools = {t.name: t for t in LoootToolkit(client=client, **kw).get_tools()}
    return tools, seen


def ok(data):
    return lambda request: httpx.Response(200, json=data)


def test_toolkit_gives_five_basetools_with_schemas():
    tools, _ = make(ok({}))
    assert set(tools) == {"looot_search_catalog", "looot_inspect", "looot_run", "looot_get_run", "looot_balance"}
    assert all(isinstance(t, BaseTool) for t in tools.values())
    assert set(tools["looot_run"].args) == {"endpoint_id", "input", "idempotency_key", "wait"}
    assert tools["looot_run"].args_schema.model_json_schema()["required"] == ["endpoint_id", "input"]


def test_read_only_toolkit_cannot_run():
    client = LoootClient("t", transport=httpx.MockTransport(ok({})))
    names = {t.name for t in LoootToolkit(client=client).get_tools(include_run=False)}
    assert "looot_run" not in names and len(names) == 4


def test_search_sends_q_limit_and_bearer_token():
    tools, seen = make(ok({"endpoints": [{"id": "serper-search"}]}))
    out = tools["looot_search_catalog"].invoke({"query": "google results", "limit": 3, "prefer": "cheapest"})
    req = seen[0]
    assert req.method == "GET" and req.url.path == "/v1/catalog/search"
    assert dict(req.url.params) == {"q": "google results", "limit": "3", "prefer": "cheapest"}
    assert req.headers["authorization"] == "Bearer test-token"
    assert json.loads(out)["endpoints"][0]["id"] == "serper-search"


def test_inspect_encodes_the_endpoint_id():
    tools, seen = make(ok({"endpoint": {}}))
    tools["looot_inspect"].invoke({"endpoint_id": "job:people.email.find"})
    assert seen[0].url.raw_path == b"/v1/operations/job%3Apeople.email.find"


def test_run_posts_the_documented_body_with_cap_and_wait():
    tools, seen = make(ok({"runId": "r1", "status": "completed", "actualCost": 0.003}))
    out = tools["looot_run"].invoke(
        {"endpoint_id": "job:people.email.find", "input": {"first_name": "Jane", "domain": "example.com"}}
    )
    req = seen[0]
    body = json.loads(req.content)
    assert req.method == "POST" and req.url.path == "/v1/runs" and req.url.params["wait"] == "20"
    assert body["endpointId"] == "job:people.email.find"
    assert body["input"] == {"first_name": "Jane", "domain": "example.com"}
    assert body["idempotencyKey"].startswith("lc-")
    assert body["fallback"] == {"enabled": True, "maxCostUsd": 1.0}
    assert json.loads(out)["status"] == "completed"


def test_run_keeps_a_caller_idempotency_key_and_can_drop_fallback():
    tools, seen = make(ok({"runId": "r"}), max_cost_usd=None)
    tools["looot_run"].fallback = False
    tools["looot_run"].invoke({"endpoint_id": "serper-search", "input": {"q": "x"}, "idempotency_key": "mine", "wait": 0})
    body = json.loads(seen[0].content)
    assert body["idempotencyKey"] == "mine" and "fallback" not in body
    assert seen[0].url.params["wait"] == "0"


def test_get_run_and_balance():
    tools, seen = make(ok({"available": 4.2, "reserved": 0, "runId": "r1"}))
    assert json.loads(tools["looot_balance"].invoke({}))["available"] == 4.2
    tools["looot_get_run"].invoke({"run_id": "r1"})
    assert [r.url.path for r in seen] == ["/v1/balance", "/v1/runs/r1"]


def test_api_error_becomes_readable_tool_output():
    def handler(request):
        return httpx.Response(
            402, json={"error": {"code": "insufficient_balance", "message": "Top up to run this", "requestId": "x"}}
        )

    tools, _ = make(handler)
    out = tools["looot_run"].invoke({"endpoint_id": "serper-search", "input": {}})
    assert "insufficient_balance" in out and "Top up" in out


def test_long_answers_are_truncated():
    tools, _ = make(ok({"blob": "x" * 50_000}))
    out = tools["looot_balance"].invoke({})
    assert len(out) < 21_000 and "truncated" in out


def test_async_invoke_uses_the_same_request():
    tools, seen = make(ok({"available": 1, "reserved": 0}))
    out = asyncio.run(tools["looot_balance"].ainvoke({}))
    assert json.loads(out)["available"] == 1 and seen[0].url.path == "/v1/balance"


def test_missing_token_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("LOOOT_TOKEN", raising=False)
    with pytest.raises(LoootError) as e:
        LoootClient(None).request("GET", "/v1/balance")
    assert e.value.code == "missing_token"
