# Observatory API (local-first)

Read-only JSON API over the committed, signed evidence tree in `data/`. Same
data as the static site, served live with search / filter / pagination and
auto-generated OpenAPI docs.

```bash
pip install -r requirements.txt
uvicorn api.app:app --reload        # http://127.0.0.1:8000
open http://127.0.0.1:8000/api/docs # interactive OpenAPI docs
```

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | counts + last update + transparency tree head |
| GET | `/api/verdicts` | latest verdict per target; filters: `kind`, `jurisdiction`, `provenance`, `drift`, `q`; paginate: `limit`, `offset` |
| GET | `/api/targets/{name}` | one target + full drift history (404 if unknown) |
| GET | `/api/advisories` | promoted advisories |
| GET | `/api/manifests`, `/api/manifests/{date}` | signed daily manifest chain (roots verifiable via Rekor) |
| GET | `/api/search?q=` | match target / model / kind |
| GET | `/api/feed.xml` | RSS 2.0 of advisories + drift |
| GET | `/api/openapi.json`, `/api/docs` | machine + human API docs |
| POST | `/api/reload` | rebuild the in-memory index after a fresh commit |

## Design

```
data/ (append-only, cosign+Rekor signed) ──lib/records──► api/store.Store ──► FastAPI
```

- **Single source of truth:** reads `data/` via `lib/records` — the same reader
  the static site uses (no duplication, no separate DB).
- **Gate-1 by construction:** serves only the PUBLIC two-tier records; interpreted
  verdict fields appear only for cleared targets / promoted advisories, and
  `withheld: true` otherwise. The gated tier lives in private staging and is
  never loaded here.
- **Local-first:** no host/secrets yet. Rate limiting and a chosen host land with
  deployment (plan phase P4).

## Not yet (later phases)
Rate limiting, SSE `/api/stream` push on new commits, and the deploy target are
P4. The frontend wiring (client-side search/filters/pagination consuming this
API) is P2.
