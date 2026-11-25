import json

import httpx
import pytest

from santa_mcp import setup_elastic
from santa_mcp.catalog import CATALOG


async def test_setup_seeds_and_searches_with_one_key_without_overwriting_documents(monkeypatch):
    monkeypatch.setenv("ELASTICSEARCH_URL", "https://es.example.test")
    monkeypatch.setenv("ELASTICSEARCH_API_KEY", "search-key")
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["authorization"] == "ApiKey search-key"
        if request.method == "HEAD":
            return httpx.Response(404)
        if request.url.path == "/_bulk":
            actions = [json.loads(line) for line in request.content.splitlines()[::2]]
            assert all(list(action) == ["create"] for action in actions)
            return httpx.Response(200, json={"items": [{"create": {"status": 409}}] * 3})
        if request.url.path.endswith("/_search"):
            toy_id = json.loads(request.content)["query"]["term"]["toy_id"]
            return httpx.Response(200, json={"hits": {"hits": [{"_source": CATALOG[toy_id]}]}})
        return httpx.Response(200, json={"acknowledged": True})

    await setup_elastic.setup(transport=httpx.MockTransport(handle))
    assert [(r.method, r.url.path) for r in requests] == [
        ("HEAD", "/santa-toy-instructions"),
        ("PUT", "/santa-toy-instructions"),
        ("POST", "/_bulk"),
        *[("POST", "/santa-toy-instructions/_search")] * 3,
    ]


async def test_setup_reports_bulk_item_failures(monkeypatch):
    monkeypatch.setenv("ELASTICSEARCH_URL", "https://es.example.test")
    monkeypatch.setenv("ELASTICSEARCH_API_KEY", "search-key")

    def handle(request):
        if request.method == "HEAD":
            return httpx.Response(200)
        assert request.url.path == "/_bulk"
        return httpx.Response(200, json={"items": [{"create": {"status": 403}}]})

    with pytest.raises(RuntimeError, match="could not be created"):
        await setup_elastic.setup(transport=httpx.MockTransport(handle))
