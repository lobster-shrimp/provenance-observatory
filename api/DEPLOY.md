# Deploying the Observatory API

The API (`api/app.py`) is a stateless FastAPI service that reads the committed
`data/` tree. It ships a `Dockerfile`; `deploy/` has configs for two hosts. Pick
one — **the account, secrets, and DNS are operator-provided** (I can't provision
those for you).

## What you provide
- A host account (Fly.io **or** Render — both free-tier capable).
- Optional: a custom domain + DNS record.
- Optional: `OBSERVATORY_RATE_LIMIT` / `OBSERVATORY_RATE_WINDOW` overrides.

## Option A — Fly.io
```bash
fly launch --copy-config --dockerfile Dockerfile --no-deploy   # name the app
fly deploy
fly status                                                     # note the URL
```
`deploy/fly.toml` sets internal port 8000, HTTPS, a `/api/status` health check,
and scale-to-zero.

## Option B — Render
New → Blueprint → point at this repo (`deploy/render.yaml`). Render builds the
Dockerfile, health-checks `/api/status`, and honors `$PORT`.

## Wire the site to the deployed API
Rebuild the static site with the public API URL so the nav/JS point at it:
```bash
OBSERVATORY_API_URL=https://your-api.example python site/build.py
```

## Data freshness
The image bakes in `data/` at build time, so the API reflects data as of the
last deploy. Both hosts auto-deploy on push, so the nightly evidence commit
redeploys automatically. For sub-deploy freshness, run a sidecar that
`git pull`s and calls `POST /api/reload`, or trigger a redeploy from the nightly
workflow.

## Live updates (SSE)
`GET /api/stream` emits a `status` event on connect, then every
`OBSERVATORY_SSE_INTERVAL` seconds, and a `change` event when `last_updated`
moves. The site's status badge upgrades to "operational" when it can reach it.

## Rate limiting
Per-IP sliding window (`OBSERVATORY_RATE_LIMIT` per `OBSERVATORY_RATE_WINDOW`s),
in-memory — fine for a single instance. If you scale to multiple instances, put
a shared limiter (or the platform's) in front.

## Gate-1
The API serves only the public two-tier records; interpreted verdicts appear
only for cleared targets. Going public is now cleared, so a deployed instance
may serve them.
