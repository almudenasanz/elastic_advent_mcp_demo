"""Seed three toy instruction documents using the demo's Elasticsearch key."""

import asyncio
import json

import httpx
from dotenv import load_dotenv

from .catalog import CATALOG
from .config import Settings
from .knowledge import INDEX, Knowledge


async def setup(transport=None):
    settings = Settings.from_env()
    elastic_url = settings.elasticsearch_url
    async with httpx.AsyncClient(
        headers={"Authorization": f"ApiKey {settings.elasticsearch_api_key}"},
        timeout=30,
        trust_env=False,
        transport=transport,
    ) as client:
        response = await client.head(f"{elastic_url}/{INDEX}")
        if response.status_code == 404:
            response = await client.put(
                f"{elastic_url}/{INDEX}",
                json={
                    "mappings": {
                        "properties": {
                            "toy_id": {"type": "keyword"},
                            "name": {"type": "keyword"},
                            "instructions": {"type": "text"},
                        }
                    },
                },
            )
        response.raise_for_status()
        lines = []
        for toy in CATALOG.values():
            lines.extend(
                [
                    json.dumps({"create": {"_index": INDEX, "_id": toy["toy_id"]}}),
                    json.dumps({k: toy[k] for k in ("toy_id", "name", "instructions")}),
                ]
            )
        response = await client.post(
            f"{elastic_url}/_bulk",
            params={"refresh": "wait_for"},
            headers={"Content-Type": "application/x-ndjson"},
            content="\n".join(lines) + "\n",
        )
        response.raise_for_status()
        items = response.json()["items"]
        if any(item["create"]["status"] not in (201, 409) for item in items):
            raise RuntimeError("Some toy documents could not be created; check index privileges")
    print(f"Created or reused {INDEX}; existing documents were preserved.")
    # Verify instructions through the same search path the MCP tools use.
    for toy_id in CATALOG:
        instructions = await Knowledge(settings, transport=transport).get_toy_instructions(toy_id)
        if instructions["instructions"] != CATALOG[toy_id]["instructions"]:
            raise ValueError(f"Existing instructions for {toy_id} differ from this demo's dataset")
    print("Verified all three toys with Elasticsearch search using ELASTICSEARCH_API_KEY.")


def main():
    load_dotenv()
    try:
        asyncio.run(setup())
    except httpx.HTTPStatusError as exc:
        raise SystemExit(
            f"Elastic setup failed with HTTP {exc.response.status_code}; "
            "check endpoints and privileges."
        ) from None
    except Exception as exc:
        # Exceptions from HTTP/auth libraries can contain headers or response bodies.
        if isinstance(exc, (ValueError, RuntimeError)):
            raise SystemExit(str(exc)) from None
        raise SystemExit(
            f"Elastic setup failed ({type(exc).__name__}); check configuration."
        ) from None
