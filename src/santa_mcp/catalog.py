"""Small fictional dataset shared by the seed script and mock workshop."""

import json
from importlib.resources import files
from typing import Literal

ElfId = Literal["pepper", "tinsel", "jingle"]
ToyId = Literal["teddy-bear", "wooden-train", "rocking-horse"]
ELVES = ("pepper", "tinsel", "jingle")
CATALOG = {
    row["toy_id"]: row
    for row in json.loads(files("santa_mcp").joinpath("catalog.json").read_text())
}

INITIAL_STOCK = {
    "fabric": 10000,
    "stuffing": 10000,
    "ribbon": 10000,
    "wood": 10000,
    "wheels": 10000,
    "paint": 10000,
    "magic-varnish": 0,
}


def missing_parts(parts: dict[str, int], stock: dict[str, int]) -> dict[str, int]:
    return {
        part: count - stock.get(part, 0)
        for part, count in parts.items()
        if stock.get(part, 0) < count
    }
