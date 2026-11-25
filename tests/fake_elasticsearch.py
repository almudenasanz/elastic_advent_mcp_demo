"""Test-only Elasticsearch HTTP boundary for seeding and instruction searches."""

import json
import sys

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from santa_mcp.knowledge import INDEX

app = FastAPI()
documents = {}
index_exists = False


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if request.url.path != "/health" and (
        request.headers.get("authorization") != "ApiKey test-search-key"
    ):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.head(f"/{INDEX}")
async def check_index():
    return Response(status_code=200 if index_exists else 404)


@app.put(f"/{INDEX}")
async def create_index():
    global index_exists
    index_exists = True
    return {"acknowledged": True}


@app.post("/_bulk")
async def bulk(request: Request):
    lines = (await request.body()).decode().splitlines()
    items = []
    for position in range(0, len(lines), 2):
        action = json.loads(lines[position])["create"]
        assert action["_index"] == INDEX
        doc_id = action["_id"]
        status = 409 if doc_id in documents else 201
        if status == 201:
            documents[doc_id] = json.loads(lines[position + 1])
        items.append({"create": {"status": status}})
    return {"items": items}


@app.post(f"/{INDEX}/_search")
async def search(request: Request):
    body = await request.json()
    toy = documents.get(body["query"]["term"]["toy_id"])
    return {"hits": {"hits": [{"_source": toy}] if toy else []}}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), access_log=False)
