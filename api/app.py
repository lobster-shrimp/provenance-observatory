"""Provenance Observatory API (FastAPI).

    uvicorn api.app:app --reload         # http://127.0.0.1:8000
    /api/docs                            # auto OpenAPI docs (the design's "API")

Read-only over the committed, signed evidence tree. Ships with per-IP rate
limiting and an SSE stream; deploy configs live in deploy/ (see api/DEPLOY.md).
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import feed as _feed  # noqa: E402

from .store import Store

# Rate limit: N requests per window seconds, per client IP. In-memory, so it's
# per-instance (fine for a single small node; use a shared limiter if you scale
# horizontally). Tune with OBSERVATORY_RATE_LIMIT / OBSERVATORY_RATE_WINDOW.
RATE_LIMIT = int(os.environ.get("OBSERVATORY_RATE_LIMIT", "120"))
RATE_WINDOW = int(os.environ.get("OBSERVATORY_RATE_WINDOW", "60"))
SSE_INTERVAL = int(os.environ.get("OBSERVATORY_SSE_INTERVAL", "15"))

app = FastAPI(
    title="Provenance Observatory API",
    version="0.1.0",
    description=("Read-only evidence API: LLM endpoint provenance/jurisdiction "
                 "verdicts, drift, advisories, and signed transparency manifests. "
                 "Interpreted verdicts are present only for cleared targets."),
    docs_url="/api/docs", openapi_url="/api/openapi.json",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["*"])

store = Store()

_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Sliding-window per-IP limiter. Docs/openapi are exempt so the UI stays usable."""
    if RATE_LIMIT > 0 and request.url.path.startswith("/api/") \
       and not request.url.path.startswith(("/api/docs", "/api/openapi")):
        ip = request.client.host if request.client else "?"
        now = time.monotonic()
        q = _hits[ip]
        while q and q[0] <= now - RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return Response(status_code=429, content="rate limit exceeded",
                            headers={"Retry-After": str(RATE_WINDOW)})
        q.append(now)
    return await call_next(request)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/api/docs")


@app.get("/api/status", tags=["meta"])
def status():
    return store.status()


@app.post("/api/reload", tags=["meta"])
def reload():
    """Rebuild the in-memory index from data/ (after a fresh nightly commit)."""
    store.reload()
    return store.status()


@app.get("/api/verdicts", tags=["verdicts"])
def verdicts(
    kind: str | None = None,
    jurisdiction: str | None = None,
    provenance: str | None = None,
    drift: bool | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Latest verdict per monitored target, filterable + paginated."""
    return store.verdicts(kind=kind, jurisdiction=jurisdiction, provenance=provenance,
                          drift=drift, q=q, limit=limit, offset=offset)


@app.get("/api/targets/{name}", tags=["verdicts"])
def target(name: str):
    """One target's latest verdict + full drift history (hot window)."""
    t = store.target(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"unknown target: {name}")
    return t


@app.get("/api/advisories", tags=["advisories"])
def advisories():
    return {"items": store.advisories()}


@app.get("/api/model-changes", tags=["verdicts"])
def model_changes():
    """Targets observed switching model identity mid-session (transcript analysis)."""
    return {"items": store.model_switches()}


@app.get("/api/manifests", tags=["transparency"])
def manifests():
    """The signed daily manifest chain (transparency log; roots verifiable via Rekor)."""
    return {"items": store.manifests()}


@app.get("/api/manifests/{date_str}", tags=["transparency"])
def manifest(date_str: str):
    m = store.manifest(date_str)
    if m is None:
        raise HTTPException(status_code=404, detail=f"no manifest for {date_str}")
    return m


@app.get("/api/search", tags=["meta"])
def search(q: str = Query("", description="match target name / model / kind")):
    return {"query": q, "results": store.search(q)}


@app.get("/api/feed.xml", tags=["meta"])
def feed():
    """RSS 2.0 feed of advisories + latest drift events (shared builder)."""
    entries = _feed.entries_from(store.advisories(), store.verdicts(drift=True, limit=20)["items"])
    return Response(content=_feed.build_rss(entries), media_type="application/rss+xml")


@app.get("/api/stream", tags=["meta"])
async def stream(request: Request, once: bool = False):
    """Server-Sent Events: current status on connect, then a `status` event each
    interval and a `change` event when the data updates (e.g. after a nightly
    commit + /api/reload). Powers the live status badge without polling.
    `?once=1` emits a single event and closes (health checks / tests)."""
    async def gen():
        last = None
        while True:
            if await request.is_disconnected():
                break
            s = store.status()
            event = "change" if (last is not None and s.get("last_updated") != last) else "status"
            last = s.get("last_updated")
            yield f"event: {event}\ndata: {json.dumps(s)}\n\n"
            if once:
                break
            await asyncio.sleep(SSE_INTERVAL)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
