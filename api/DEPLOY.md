# Deploying the Observatory API

> **Live (2026-08-14):** deployed to **Google Cloud Run** —
> `https://provenance-observatory-api-513338163479.us-central1.run.app`
> (project `gen-lang-client-0992245391`, region `us-central1`, `--allow-unauthenticated`,
> scale-to-zero). No secrets (serves public `data/`; the image bakes `data/` in at build,
> so a redeploy refreshes it). Redeploy: `gcloud run deploy provenance-observatory-api
> --source . --project gen-lang-client-0992245391 --region us-central1
> --allow-unauthenticated`. The Fly.io / Render configs below remain valid alternatives.
>
> **Auto-redeploy** (keyless — Workload Identity Federation, no stored key) happens
> two ways: (1) `.github/workflows/deploy-api.yml` on human/merge pushes to `main`
> touching `data/**`, `api/**`, `lib/**`, `Dockerfile`, `requirements.txt`; and (2) the
> **nightly workflow redeploys directly** at the end of its probe job when the `data:`
> commit changed something — because that commit is pushed with `GITHUB_TOKEN`, which
> (GitHub anti-recursion) does NOT trigger `deploy-api.yml`. Both use the same WIF
> provider + deploy SA. One-time GCP setup (creates a repo-scoped deploy SA + WIF pool):
>
> ```bash
> PROJECT=gen-lang-client-0992245391; SA=obs-api-deployer
> SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"; POOL=github-pool; PROVIDER=github-provider
> REPO=lobster-shrimp/provenance-observatory
> PNUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
> gcloud services enable iamcredentials.googleapis.com sts.googleapis.com run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project "$PROJECT"
> gcloud iam service-accounts create "$SA" --project "$PROJECT" --display-name "Observatory API Cloud Run deployer"
> for R in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer roles/storage.admin roles/iam.serviceAccountUser; do
>   gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA_EMAIL" --role="$R" --condition=None; done
> gcloud iam workload-identity-pools create "$POOL" --project "$PROJECT" --location global --display-name "GitHub pool"
> gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --project "$PROJECT" --location global \
>   --workload-identity-pool "$POOL" --display-name "GitHub provider" \
>   --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
>   --attribute-condition "assertion.repository=='$REPO'" --issuer-uri "https://token.actions.githubusercontent.com"
> gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" --project "$PROJECT" --role roles/iam.workloadIdentityUser \
>   --member "principalSet://iam.googleapis.com/projects/$PNUM/locations/global/workloadIdentityPools/$POOL/attribute.repository/$REPO"
> ```
>
> The workflow's `workload_identity_provider` is
> `projects/<PNUM>/locations/global/workloadIdentityPools/github-pool/providers/github-provider`
> and `service_account` is `$SA_EMAIL`.

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
