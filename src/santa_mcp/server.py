"""The MCP application: workshop operations and OpenTelemetry."""

import argparse
import logging
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.trace import get_current_span
from starlette.requests import Request
from starlette.responses import JSONResponse

from .catalog import CATALOG, ElfId, ToyId, missing_parts
from .config import Settings
from .knowledge import Knowledge
from .middleware import UsageTelemetryMiddleware
from .otel import configure_logging, tracer

logger = logging.getLogger(__name__)


def create_server(
    settings: Settings | None = None, workshop_transport=None, knowledge_transport=None
):
    settings = settings or Settings.from_env()
    knowledge = Knowledge(settings, transport=knowledge_transport)
    workshop = None

    @asynccontextmanager
    async def lifespan(server):
        nonlocal workshop
        async with httpx.AsyncClient(
            base_url=settings.workshop_url,
            transport=workshop_transport,
            timeout=15,
            trust_env=False,
        ) as workshop:
            logger.info("Workshop ready; toy instructions are stored in Elasticsearch")
            yield {}

    mcp = FastMCP("Santa's Workshop", lifespan=lifespan)
    mcp.add_middleware(UsageTelemetryMiddleware())

    async def api(method, path, **kwargs):
        try:
            response = await workshop.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                raise ToolError(
                    "Parts unavailable; the workshop could not build this toy"
                ) from None
            raise ToolError("Workshop API returned an error") from None
        except httpx.RequestError:
            raise ToolError("Workshop API unavailable; check WORKSHOP_URL") from None

    @mcp.tool
    async def get_toy_instructions(toy_id: ToyId, elf_id: ElfId) -> dict:
        """Read assembly instructions. elf_id is a simulated demo identity, not authentication."""
        return await knowledge.get_toy_instructions(toy_id)

    @mcp.tool
    async def check_parts(toy_id: ToyId, elf_id: ElfId) -> dict:
        """Check whether the workshop has parts for one toy without reserving stock."""
        stock = await api("GET", "/stock")
        missing = missing_parts(CATALOG[toy_id]["parts"], stock)
        return {"toy_id": toy_id, "available": not missing, "missing_parts": missing}

    @mcp.tool
    async def build_present(toy_id: ToyId, elf_id: ElfId) -> dict:
        """Build one present: fetch instructions, check parts, then manufacture it.

        This changes mock stock. Call once per desired present; do not blindly retry builds.
        elf_id is a fictional identity. The server always performs its own stock check.
        """
        get_current_span().set_attribute("santa.toy.id", toy_id)
        await knowledge.get_toy_instructions(toy_id)
        with tracer.start_as_current_span("workshop.check_parts"):
            stock = await api("GET", "/stock")
            missing = missing_parts(CATALOG[toy_id]["parts"], stock)
            if missing:
                logger.warning("Parts missing", extra={"santa.toy.id": toy_id})
                raise ToolError("Parts unavailable: " + ", ".join(sorted(missing)))
        with tracer.start_as_current_span("workshop.build"):
            result = await api("POST", "/build", json={"toy_id": toy_id, "elf_id": elf_id})
        return result

    @mcp.resource("santa://workshop/handbook")
    def handbook() -> str:
        return (
            "Welcome to Santa's workshop. Elves: pepper, tinsel, jingle. "
            "Toys: teddy-bear (quick), wooden-train (slow paint), rocking-horse "
            "(missing magic-varnish). build_present fetches instructions and checks stock "
            "before building. Identities are simulated; stock resets on workshop restart. "
            "Toy instructions are retrieved from Elasticsearch."
        )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    return mcp


def main():
    load_dotenv()
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()
    mcp = create_server()
    if args.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
    else:
        # HTTPX instrumentation is loaded by opentelemetry-instrument. Explicit ASGI
        # middleware gives both services an incoming HTTP span and W3C propagation.
        app = OpenTelemetryMiddleware(mcp.http_app(stateless_http=True))
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)
