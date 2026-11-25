"""Shared usage telemetry for workshop tools and resources."""

import asyncio
import logging
import time

from fastmcp.server.middleware import Middleware
from opentelemetry.trace import Status, StatusCode

from .catalog import ELVES
from .otel import instruments, tracer

logger = logging.getLogger(__name__)


def elf_for(context) -> str:
    arguments = getattr(context.message, "arguments", None) or {}
    elf = arguments.get("elf_id")
    return elf if isinstance(elf, str) and elf in ELVES else "unknown"


class UsageTelemetryMiddleware(Middleware):
    async def _measure(self, context, call_next, method, target):
        # These are demo-specific attributes, not a claim of stable MCP semantic conventions.
        attributes = {
            "mcp.method": method,
            "mcp.target": target,
            "santa.elf.id": elf_for(context),
        }
        start = time.perf_counter()
        outcome = "success"
        with tracer.start_as_current_span(
            f"{method}:{target}",
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                result = await call_next(context)
                # Some upstream/tool implementations return an MCP error instead of raising.
                if getattr(result, "is_error", False) or getattr(result, "isError", False):
                    outcome = "error"
                    span.set_status(Status(StatusCode.ERROR, "Tool returned an error"))
                return result
            except asyncio.CancelledError:
                outcome = "cancelled"
                span.set_status(Status(StatusCode.ERROR, "Request cancelled"))
                raise
            except Exception as exc:
                outcome = "error"
                # Record the class only: upstream exception strings may include sensitive data.
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            finally:
                duration = time.perf_counter() - start
                attrs = {**attributes, "mcp.outcome": outcome}
                span.set_attribute("mcp.outcome", outcome)
                requests, latency = instruments()
                requests.add(1, attrs)
                latency.record(duration, attrs)
                # The log is emitted while the span is active, preserving trace correlation.
                logger.info(
                    "MCP invocation completed tool=%s elf=%s outcome=%s duration_ms=%.1f",
                    target,
                    attributes["santa.elf.id"],
                    outcome,
                    duration * 1000,
                    extra={**attrs, "event.name": "mcp.invocation", "duration_ms": duration * 1000},
                )

    async def on_call_tool(self, context, call_next):
        return await self._measure(context, call_next, "tools", context.message.name)

    async def on_read_resource(self, context, call_next):
        return await self._measure(context, call_next, "resources", str(context.message.uri))
