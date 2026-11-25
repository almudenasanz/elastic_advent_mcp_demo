"""Drive reproducible MCP calls without requiring an LLM or a particular AI client."""

import argparse
import asyncio
import json
import time

from fastmcp import Client


async def run(url, scenario="all"):
    records = []
    async with Client(url, timeout=30) as client:
        tools = await client.list_tools()
        expected_tools = {"get_toy_instructions", "check_parts", "build_present"}
        if {tool.name for tool in tools} != expected_tools:
            raise RuntimeError("Unexpected MCP tool catalog")
        resources = await client.read_resource("santa://workshop/handbook")
        print(json.dumps({"tools": sorted(expected_tools), "handbook": resources[0].text}))

        async def call(tool, toy, elf, expected="success"):
            start = time.perf_counter()
            result = await client.call_tool(
                tool,
                {"toy_id": toy, "elf_id": elf},
                raise_on_error=False,
            )
            text = " ".join(getattr(block, "text", "") for block in result.content)
            actual = "error" if result.is_error else "success"
            record = {
                "tool": tool,
                "toy": toy,
                "elf": elf,
                "outcome": actual,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                "expected": expected,
            }
            records.append(record)
            print(json.dumps(record), flush=True)
            if actual != expected:
                raise RuntimeError(
                    f"Expected {expected}, got {actual}: {text}. "
                    "For repeatable results restart the workshop API to reset stock."
                )

        if scenario in ("all", "builds"):
            await call("get_toy_instructions", "teddy-bear", "tinsel")
            await call("build_present", "teddy-bear", "tinsel")
            await call("build_present", "wooden-train", "jingle")
            await call("build_present", "rocking-horse", "jingle", "error")
        if scenario in ("all", "stock"):
            for _ in range(5):
                await call("check_parts", "teddy-bear", "pepper")
            await call("check_parts", "teddy-bear", "tinsel")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument(
        "--scenario", choices=["all", "builds", "stock", "discovery"], default="all"
    )
    args = parser.parse_args()
    try:
        records = asyncio.run(run(args.url, args.scenario))
    except Exception as exc:
        raise SystemExit(f"Demo run failed: {exc}") from None
    print(f"Verified {len(records)} tool calls and the workshop resource.")
