"""A mock manufacturing API: real HTTP calls, fictional toys and inventory."""

import asyncio
import logging
import os
import uuid

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.trace import get_current_span
from pydantic import BaseModel

from .catalog import CATALOG, INITIAL_STOCK, ElfId, ToyId, missing_parts
from .otel import configure_logging, tracer

logger = logging.getLogger(__name__)


class BuildRequest(BaseModel):
    toy_id: ToyId
    elf_id: ElfId


def create_app(paint_delay_seconds: float = 2.0):
    if paint_delay_seconds < 0:
        raise ValueError("PAINT_DELAY_SECONDS must be non-negative")
    app = FastAPI(title="Santa's mock workshop API")
    app.add_middleware(OpenTelemetryMiddleware)
    stock = INITIAL_STOCK.copy()
    lock = asyncio.Lock()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/stock")
    async def get_stock():
        async with lock:
            return stock.copy()

    @app.post("/build")
    async def build(request: BuildRequest):
        get_current_span().set_attributes(
            {
                "santa.toy.id": request.toy_id,
                "santa.elf.id": request.elf_id,
            }
        )
        # The API is authoritative: recheck and decrement together to prevent races.
        parts = CATALOG[request.toy_id]["parts"]
        async with lock:
            if missing := missing_parts(parts, stock):
                raise HTTPException(409, {"missing_parts": missing})
            for part, amount in parts.items():
                stock[part] -= amount
        try:
            with tracer.start_as_current_span("workshop.assemble"):
                await asyncio.sleep(0.02)
            with tracer.start_as_current_span("workshop.paint"):
                delay = paint_delay_seconds if request.toy_id == "wooden-train" else 0.02
                logger.info(
                    "Paint booth started",
                    extra={
                        "event.name": "workshop.paint.started",
                        "santa.toy.id": request.toy_id,
                        "santa.elf.id": request.elf_id,
                        "paint.booth.id": "north-pole-1",
                        "paint.delay_seconds": delay,
                        "paint.simulated_slowdown": delay > 0.02,
                    },
                )
                await asyncio.sleep(delay)
            with tracer.start_as_current_span("workshop.wrap"):
                await asyncio.sleep(0.02)
        except BaseException:
            # This mock can undo a cancelled simulated build. Real manufacturing needs
            # durable job state and idempotency rather than automatic HTTP retries.
            async with lock:
                for part, amount in parts.items():
                    stock[part] += amount
            raise
        logger.info(
            "Present built",
            extra={
                "event.name": "workshop.present_built",
                "santa.toy.id": request.toy_id,
                "santa.elf.id": request.elf_id,
                "santa.parts.required": sum(parts.values()),
            },
        )
        return {"build_id": str(uuid.uuid4()), "toy_id": request.toy_id, "status": "built"}

    return app


def main():
    load_dotenv()
    configure_logging()
    app = create_app(float(os.getenv("PAINT_DELAY_SECONDS", "2")))
    uvicorn.run(
        app,
        host=os.getenv("WORKSHOP_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8001")),
        access_log=False,
    )
