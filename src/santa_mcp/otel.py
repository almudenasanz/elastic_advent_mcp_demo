"""Custom OpenTelemetry instruments; EDOT configures their providers at startup."""

import logging
from functools import lru_cache

from opentelemetry import metrics, trace

tracer = trace.get_tracer("santas-workshop")


@lru_cache(maxsize=1)
def instruments():
    meter = metrics.get_meter("santas-workshop")
    return (
        meter.create_counter("mcp.requests", unit="1", description="Completed MCP invocations"),
        meter.create_histogram(
            "mcp.request.duration", unit="s", description="MCP invocation duration"
        ),
    )


def configure_logging():
    # EDOT adds an OTLP logging handler when launched with opentelemetry-instrument.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # Avoid request URLs or upstream response bodies in routine third-party logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
