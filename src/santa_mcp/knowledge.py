"""Retrieve toy assembly instructions directly from Elasticsearch."""

import logging

import httpx
from fastmcp.exceptions import ToolError

from .config import Settings
from .otel import tracer

INDEX = "santa-toy-instructions"
logger = logging.getLogger(__name__)


class Knowledge:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    async def get_toy_instructions(self, toy_id: str):
        with tracer.start_as_current_span("knowledge.get_toy_instructions"):
            try:
                async with httpx.AsyncClient(
                    headers={"Authorization": f"ApiKey {self.settings.elasticsearch_api_key}"},
                    transport=self.transport,
                    timeout=15,
                    trust_env=False,
                ) as client:
                    response = await client.post(
                        f"{self.settings.elasticsearch_url}/{INDEX}/_search",
                        json={
                            "query": {"term": {"toy_id": toy_id}},
                            "_source": ["toy_id", "name", "instructions"],
                            "size": 1,
                        },
                    )
                    response.raise_for_status()
                    hits = response.json()["hits"]["hits"]
                if not hits:
                    raise ToolError("Toy instructions not found; run santa-setup-elastic first")
                instructions = hits[0]["_source"]
                if instructions.get("toy_id") != toy_id or not instructions.get("instructions"):
                    raise ToolError(
                        "Invalid toy instructions; check the santa-toy-instructions index"
                    )
                return instructions
            except ToolError:
                raise
            except Exception as exc:
                logger.error(
                    "Elasticsearch instructions search failed",
                    extra={"error.type": type(exc).__name__},
                )
                raise ToolError(
                    "Elasticsearch instructions search failed; check its URL, key, and seeded index"
                ) from None
