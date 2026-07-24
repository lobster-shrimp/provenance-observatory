"""Provenance Observatory API (FastAPI, local-first).

    uvicorn api.app:app --reload         # http://127.0.0.1:8000
    /api/docs                            # auto OpenAPI docs (the design's "API")

Read-only over the committed, signed evidence tree. Rate limiting + a chosen
host come with deployment (plan phase P4); locally it's open and open-CORS so
the static site's JS can fetch it.
"""
from __future__ import annotations
import html
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .store import Store

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
    """RSS 2.0 feed of advisories + latest drift events."""
    items = []
    for a in store.advisories():
        items.append((f"{a.get('advisory_id','advisory')}: {a.get('target','')}",
                      a.get("summary") or a.get("title") or "Verdict change advisory.",
                      a.get("promoted_at", "")))
    for it in store.verdicts(drift=True, limit=20)["items"]:
        items.append((f"Drift: {it['target']}",
                      f"Fingerprint changed; last checked {it['last_checked']}.",
                      it["last_checked"]))
    body = "".join(
        f"<item><title>{xml_escape(t)}</title>"
        f"<description>{xml_escape(d)}</description>"
        f"<pubDate>{xml_escape(str(p))}</pubDate>"
        f"<guid isPermaLink=\"false\">{xml_escape(t + str(p))}</guid></item>"
        for t, d, p in items)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<rss version="2.0"><channel>'
           '<title>Provenance Observatory</title>'
           '<description>LLM provenance/jurisdiction advisories and drift.</description>'
           '<link>https://github.com/lobster-shrimp/provenance-observatory</link>'
           f'{body}</channel></rss>')
    return Response(content=xml, media_type="application/rss+xml")
