import json

import httpx
import pytest
from fastmcp.exceptions import ToolError

from santa_mcp.config import Settings
from santa_mcp.knowledge import Knowledge


def test_search_requires_url_and_key():
    with pytest.raises(ValueError, match="ELASTICSEARCH_URL and ELASTICSEARCH_API_KEY"):
        Settings()


def test_ingest_endpoint_cannot_be_used_for_search():
    with pytest.raises(ValueError, match="not the ingest endpoint"):
        Settings(
            elasticsearch_url="https://example.ingest.us-central1.gcp.elastic.cloud/_es",
            elasticsearch_api_key="test-search-key",
        )


async def test_search_uses_exact_toy_id_and_elasticsearch_key():
    instructions = {"toy_id": "wooden-train", "name": "Wooden train", "instructions": "Paint red"}

    def handle(request):
        assert request.url.path == "/santa-toy-instructions/_search"
        assert request.headers["authorization"] == "ApiKey test-search-key"
        assert json.loads(request.content) == {
            "query": {"term": {"toy_id": "wooden-train"}},
            "_source": ["toy_id", "name", "instructions"],
            "size": 1,
        }
        return httpx.Response(200, json={"hits": {"hits": [{"_source": instructions}]}})

    knowledge = Knowledge(
        Settings(
            elasticsearch_url="https://es.example.test", elasticsearch_api_key="test-search-key"
        ),
        transport=httpx.MockTransport(handle),
    )
    assert await knowledge.get_toy_instructions("wooden-train") == instructions


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": "secret-upstream-error"}, "search failed"),
        (500, {"error": "secret-upstream-error"}, "search failed"),
        (200, {"hits": {"hits": []}}, "not found"),
        (200, {"hits": {"hits": [{"_source": {"toy_id": "another-toy"}}]}}, "Invalid"),
    ],
)
async def test_failed_search_is_visible_without_falling_back(status, body, message, caplog):
    knowledge = Knowledge(
        Settings(
            elasticsearch_url="https://es.example.test", elasticsearch_api_key="test-search-key"
        ),
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    with pytest.raises(ToolError, match=message) as failure:
        await knowledge.get_toy_instructions("wooden-train")
    assert "secret-upstream-error" not in str(failure.value) + caplog.text
    assert "test-search-key" not in str(failure.value) + caplog.text
