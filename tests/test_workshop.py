import asyncio
import json

import httpx
from fastmcp import Client
from opentelemetry.trace import StatusCode

from santa_mcp.catalog import CATALOG
from santa_mcp.config import Settings
from santa_mcp.server import create_server
from santa_mcp.workshop import create_app


def server(**kwargs):
    def search(request):
        toy_id = json.loads(request.content)["query"]["term"]["toy_id"]
        return httpx.Response(200, json={"hits": {"hits": [{"_source": CATALOG[toy_id]}]}})

    return create_server(
        Settings(
            elasticsearch_url="https://es.example.test",
            elasticsearch_api_key="test-search-key",
            **kwargs,
        ),
        workshop_transport=httpx.ASGITransport(app=create_app(0)),
        knowledge_transport=httpx.MockTransport(search),
    )


async def test_build_is_one_tool_call_with_children_and_correlated_log(telemetry):
    async with Client(server()) as client:
        result = await client.call_tool(
            "build_present", {"toy_id": "teddy-bear", "elf_id": "pepper"}
        )
        assert result.data["status"] == "built"
        stock = await client.call_tool(
            "check_parts",
            {"toy_id": "teddy-bear", "elf_id": "pepper"},
            raise_on_error=False,
        )
        assert not stock.is_error
    spans = telemetry[0].get_finished_spans()
    invocation = next(s for s in spans if s.name == "tools:build_present")
    assert invocation.attributes["santa.elf.id"] == "pepper"
    assert invocation.attributes["mcp.outcome"] == "success"
    # FastMCP also emits a native tools/call span between the middleware and handler.
    assert {"knowledge.get_toy_instructions", "workshop.check_parts", "workshop.build"} <= {
        s.name for s in spans if s.context.trace_id == invocation.context.trace_id
    }
    logs = [r.log_record for r in telemetry[2].get_finished_logs()]
    completion = next(r for r in logs if (r.attributes or {}).get("mcp.target") == "build_present")
    assert completion.trace_id == invocation.context.trace_id
    assert completion.span_id == invocation.context.span_id


async def test_repeated_stock_checks_record_usage_per_elf(telemetry):
    def request_counts():
        data = telemetry[1].get_metrics_data()
        if data is None:
            return {}
        return {
            p.attributes["santa.elf.id"]: p.value
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == "mcp.requests"
            for p in metric.data.data_points
            if p.attributes.get("mcp.target") == "check_parts"
            and p.attributes.get("mcp.outcome") == "success"
        }

    before = request_counts()
    async with Client(server()) as client:
        args = {"toy_id": "teddy-bear", "elf_id": "pepper"}
        for _ in range(6):
            result = await client.call_tool("check_parts", args)
            assert result.data["available"]
        other = await client.call_tool("check_parts", {**args, "elf_id": "tinsel"})
        assert other.data["available"]
    spans = [s for s in telemetry[0].get_finished_spans() if s.name == "tools:check_parts"]
    assert len(spans) == 7
    assert all(s.attributes["mcp.outcome"] == "success" for s in spans)
    after = request_counts()
    assert after["pepper"] - before.get("pepper", 0) == 6
    assert after["tinsel"] - before.get("tinsel", 0) == 1


async def test_missing_parts_returns_mcp_error_and_does_not_build(telemetry):
    async with Client(server()) as client:
        result = await client.call_tool(
            "build_present",
            {"toy_id": "rocking-horse", "elf_id": "jingle"},
            raise_on_error=False,
        )
        assert result.is_error
        assert "magic-varnish" in result.content[0].text
    spans = telemetry[0].get_finished_spans()
    assert not any(s.name == "workshop.build" for s in spans)
    invocation = next(s for s in spans if s.name == "tools:build_present")
    assert invocation.attributes["mcp.outcome"] == "error"
    assert invocation.status.status_code == StatusCode.ERROR


async def test_unknown_elf_is_rejected_and_handbook_is_readable():
    async with Client(server()) as client:
        rejected = await client.call_tool(
            "check_parts",
            {"toy_id": "teddy-bear", "elf_id": "santa"},
            raise_on_error=False,
        )
        assert rejected.is_error
        for _ in range(3):
            result = await client.read_resource("santa://workshop/handbook")
            assert "simulated" in result[0].text
        await client.call_tool("check_parts", {"toy_id": "teddy-bear", "elf_id": "pepper"})


async def test_workshop_atomically_rechecks_stock_under_concurrent_builds(monkeypatch):
    from santa_mcp.catalog import INITIAL_STOCK

    monkeypatch.setitem(INITIAL_STOCK, "wheels", 8)
    app = create_app(0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        # Each train needs four wheels. Stock must never become negative, even when
        # requests bypass the MCP stock precheck and arrive together.
        count = INITIAL_STOCK["wheels"] // 4
        responses = await asyncio.gather(
            *(
                api.post("/build", json={"toy_id": "wooden-train", "elf_id": "pepper"})
                for _ in range(count + 2)
            )
        )
        assert sum(r.status_code == 200 for r in responses) == count
        assert sum(r.status_code == 409 for r in responses) == 2
        stock = (await api.get("/stock")).json()
        assert stock["wheels"] == 0
        assert min(stock.values()) >= 0
