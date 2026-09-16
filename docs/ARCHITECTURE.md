# Architecture & decision record

This scaffold implements the approved design for the Provenance Observatory. The
full design doc + eng review live in the gstack project store; this file is the
in-repo summary so the load-bearing decisions travel with the code.

## Approach A (chosen)

GitHub-native: nightly Actions cron → provenance-probe (black-box CLI) →
verdicts committed to git → GitHub Pages renders from `data/` → drift opens a
draft advisory. Zero servers. Strict subset of the hosted service (B) and the
federated feed (C); graduate to B only if target count or probe-schedule
privacy forces it.

## Load-bearing decisions (and where they live in code)

| ID | Decision | Where |
|----|----------|-------|
| T5 | Two-tier publication: neutral evidence public immediately, interpreted verdict gated by disclosure window | `lib/verdict.py` |
| T7 | Consume provenance-probe as a black-box CLI, never import internals | `runner/run.py` |
| T9 | Baseline pinned; advances only on normal advisory close or post-stability blessing — **NOT** on an UNSTABLE-triggered close | `lib/baseline.py` |
| U1 | First targets OpenRouter + Together, behavioral OFF, `public:false` until Gate 1 | `targets.yaml` |
| U2 | Per-run probe cap + monthly spend ceiling (abort → no-verdict); 90-day hot window then weekly rollup, raw log kept forever | `targets.yaml`, `runner/run.py`, `site/build.py` |
| — | Run-outcome policy: retry once, then commit `no-verdict{reason}` — no silent gaps | `runner/run.py` |
| — | Workflow security: schedule/dispatch triggers only; env-scoped secrets; least-privilege staging PAT | `.github/workflows/observatory.yml` |
| P2b | Publication policy: the signer refuses proxy (`via_omniroute`) records without calibration+disclosure, and quarantines CONTRADICTED cross-checks — never auto-published | `lib/publish_policy.py`, `lib/signing.py`, `lib/records.py` |
| #50 | Drift persistence: private staging repo round-trip (least-privilege PAT) so the diff + UNSTABLE damper persist; public `pinned.json` fingerprint; **DRAFT-only dry-run** gate (`OBSERVATORY_ADVISORY_LIVE`, default off) | `lib/staging_sync.py`, `runner/run.py` |

> **Note:** T5's two-tier withholding + disclosure-window was **reversed to full
> transparency** — the observatory now publishes the complete work (measurements
> AND the interpreted verdict) as collected. Accuracy is served by transparency +
> safeguards (controls, published FP rate, confidence labels, corrections), not by
> hiding evidence. The P2b policy below is the one narrow exception: what the
> *signer* will and won't certify.

## Drift persistence — making silent-swap detection actually fire (#50)

Silent-swap detection depends on state that MUST survive between nightly runs.
On a GitHub-hosted runner `STAGING_DIR` (`~/.provenance-observatory-staging`) is
empty at the start of every job, so without persistence `check_drift` re-seeds
its baseline every night and never fires. The fix (locked design: option b +
public fingerprint + DRAFT-only dry-run):

- **Private staging repo round-trip** (`lib/staging_sync.py`, wired into
  `runner/run.py`): when `STAGING_PAT` **and** `OBSERVATORY_STAGING_REPO` are
  set, the run `git clone --depth 1`s the private staging repo into `STAGING_DIR`
  before any target, and `git add -A && commit && push`es it back after. What
  round-trips: `<target>/baseline.json` (the raw bundle `monitor` diffs against),
  `<target>/state.json` (`TargetState`: pinned_baseline, recent_fingerprints,
  UNSTABLE state), draft advisories, and `advisory-counter.json`. This keeps the
  **gated raw bundles + tokenizer vectors PRIVATE** (never in public `data/`)
  while making the diff + the UNSTABLE damper persist. **No PAT / no URL → clean
  local-only fallback** (current behavior; local dev + CI need no network). The
  PAT is least-privilege (staging repo only), never logged, and scrubbed from the
  cloned `.git/config` remote.
- **Public pinned baseline** (`data/<target>/pinned.json` = `{target,
  fingerprint_id, pinned_at}`): the pinned `fingerprint_id` only — a public
  pinned baseline is itself a transparency feature. It carries **no gated
  content** and does **not** drive the diff (the private `baseline.json` does).
- **Migration seed**: on a first run with an empty staging repo, each target's
  `baseline.json`/`state.json` is seeded from its most recent committed
  `verdict.json`, so day-1 does not re-seed drift from nothing. The 45 days of
  `drift_seen:false` history stays (append-only); real drift detection begins
  from the seed.

### Dry-run gate — `OBSERVATORY_ADVISORY_LIVE` (default `0`, the non-negotiable)

`monitor.fingerprint()` hashes `header_shape_hash` + `error_signature`, so a CDN
or gateway header change flips the `fingerprint_id` with **no model change**
(deepseek-direct moved several times/week on pure wire noise). Promoting a public
advisory on that noise would manufacture a **false public accusation** — the
project's cardinal sin (zero-FP-accusation, CONTRADICTED-quarantine). So:

- **`OBSERVATORY_ADVISORY_LIVE` unset/`0` (default):** drift still opens/updates a
  **DRAFT** in the private staging repo and logs a `::notice::`, but does **not**
  promote to a numbered public `MPA-YYYY-NNN`, does not write `data/advisories/`,
  and does not fire the GitHub-issue alert.
- **`=1`:** full behavior — the drift path promotes (`advisory.promote`, same
  machine guard as `runner/promote.py`: via_omniroute/CONTRADICTED evidence is
  refused) and writes `data/advisories/<MPA>.json`, which the workflow's "Alert on
  model switch" step detects.

**Real drift vs wire noise.** The **UNSTABLE state machine** (`lib/baseline.py`)
is the damper and MUST persist for this to be safe: a fingerprint change opens a
DRAFT (not a public advisory); ≥3 distinct fingerprints or ≥3 alternations/14d
flips the target to **UNSTABLE**, which auto-closes the open draft and does
**not** advance the pinned baseline. Confirm those thresholds swallow the
observed ~few-times-a-week movement over a **dry-run week** before an operator
flips `OBSERVATORY_ADVISORY_LIVE=1`. **T9 holds throughout:** the pinned baseline
advances only on a NORMAL advisory close or a post-stability blessing — never on
an UNSTABLE-triggered close.

## Publication policy — what the signer certifies (P2b)

Full transparency publishes everything, but the cosign/Rekor-**signed** manifest
carries a stronger claim ("this is our certified verdict"). `lib/publish_policy.py`
gates it:

- **`measurement_path: direct | via_omniroute`** is a first-class record field.
  A record with an `omniroute` block defaults to `via_omniroute` — a proxy
  measurement can't be laundered as first-party by omitting the field.
- A **`via_omniroute`** record is signable only with a **passing calibration**
  (`omniroute.calibration.passed is True`) **and** routing disclosure
  (`router_headers`/`router_claim`). Measuring through a router that injects a
  hidden ~2000-token prompt is a proxy measurement; without calibration proving
  the injection cancels (see the probe's calibration gate), it can't be certified.
- A **CONTRADICTED** router-vs-fingerprint cross-check (nested or top-level) is
  quarantined regardless — it's an accusation about a named third party, held for
  human review, **never auto-published**.

Quarantined records are **excluded from `entries`/`manifest_root`** (the signature
never covers them), **filtered from the public verdict loaders** (site + API both
read `records.load_target_records` — so a quarantined record never renders as a
verdict), and surfaced with a reason in the transparency-log **Quarantined
(uncertified)** section. `transcript.json` (mid-session model-switch findings) runs
through the same policy and is now signed too. The promote path has a machine guard
so a quarantine-worthy advisory can't be numbered and published as a back door.

## Provider-attribution registry (public, signed)

The public `domain → operating entity → jurisdiction` registry is **generated by the
probe** (`provenance-probe build-registry`, from its `corpus.py` — the single source
of truth) and **signed + published by the observatory**. This split follows T7: the
probe owns the intelligence, the observatory owns signing (it has the cosign/Rekor
machinery) and publication.

- **Runner** (`runner/build_registry.py`, wired into `run.py`): each nightly run
  regenerates the registry to a TEMP file, gates it on `provenance-probe
  verify-registry`, signs it (`lib/signing.sign_manifest`, cosign keyless →
  `.cosign.bundle`), then **atomically promotes** the registry + bundle into
  `data/registry/`. The drift gate is fail-closed for **publication**, not just
  signing: a registry that no longer matches `corpus.py` is never written to the
  served path, and a `.cosign.bundle` never outlives the bytes it signed (a
  deterministic no-change run leaves the committed registry + signature untouched).
- **Served** at `/api/registry` (with a `signed` flag); committed into `data/` like
  the manifests, so Pages/API expose a verifiable artifact.
- **Honesty**: entries are sub-CONFIRMED static pointers (who a domain is registered
  to), never a measured provenance verdict; aggregators are `jurisdiction:
  unresolved`. The signer certifies the artifact, not a verdict.

**Probe install / version:** the nightly workflow installs the probe from the
**pinned PyPI release** — `pip install -r requirements.txt`
(`llm-provenance-probe==0.28.0` + pyyaml) — a reproducible, non-moving build
(`.github/workflows/observatory.yml`). So `build-registry` + `build-catalog` are
present each night and the registry + catalog are **built and signed nightly**. Bump
the pin in `requirements.txt` to adopt a new probe release. The graceful-degrade no-op
path still exists as a safety net if an older probe is ever installed. (The
eval-harness scripts — `refresh-engine-eval.sh` / `controls-selftest.sh` — still clone
source, because `eval/` is not packaged in the wheel.)

## LLM-API catalog (public, signed, continuously refreshed)

A searchable table of inference APIs × their models × model-card facts (context
window, price, modalities, open-weights, dates), **joined with corpus.py provenance /
jurisdiction** — the column no generic model catalog has. Like the registry, the
probe GENERATES it (`provenance-probe build-catalog`, which fetches models.dev — MIT,
`github.com/sst/models.dev` — and joins each provider's `api` host to corpus.py) and
the observatory signs + publishes it. This is what makes the catalog *continuously
updated*: the probe ships a point-in-time snapshot, the observatory refreshes it
nightly.

- **Runner** (`runner/build_catalog.py`, wired into `run.py`): each nightly run
  regenerates the catalog to a TEMP file, **gates it fail-closed on well-formedness +
  non-emptiness** (a models.dev outage makes `build-catalog` exit non-zero, or yields
  an empty doc — either way it is a clean no-op, never an empty publish over a good
  one), signs it (`lib/signing.sign_manifest`), then **atomically promotes** the
  catalog + bundle into `data/catalog/`.
- **Deterministic vs not:** unlike the registry (deterministic from corpus.py, so it
  has a `verify-registry` drift gate and a no-change run keeps its signature), the
  catalog tracks a **non-deterministic** upstream that changes daily — so there is no
  drift gate; a byte-identical day still keeps the committed signature, and any real
  change is re-signed and promoted.
- **Served** at `/api/catalog` (with a `signed` flag); committed into `data/` like the
  manifests + registry.
- **Honesty**: each provenance pointer is sub-CONFIRMED (who a host is registered to),
  never a measured verdict; aggregators are `jurisdiction: unresolved`. The signer
  certifies the artifact (the join was done faithfully), not a verdict.

**Install / refresh (corrected):** because CI installs the probe from `git@main` (see
the registry note above), `build-catalog` runs each night — the catalog is **refreshed
+ signed nightly today**. A **seed** `data/catalog/catalog.json` is committed (unsigned,
generated once from the probe) so `/api/catalog` and the public **Pages table**
(`catalog.html`) are live immediately; the first nightly run replaces the seed with a
signed, refreshed copy. The workflow installs the pinned `llm-provenance-probe==0.28.0`
from PyPI (reproducible), so the refresh runs on a fixed probe version.

## Public catalog page (`site/build.py` → `catalog.html`)

`build()` renders a public **running table** from the signed catalog artifact: the
**Chinese-origin (PRC-operated) inference APIs** — the mission subset — one model row
each (jurisdiction, provider, API host, model, context, price, weights), with a
client-side text filter. The complete multi-thousand-model catalog (aggregators +
first-party included) is at `/api/catalog` and fully searchable in the probe's
`/catalog` tool; the page links to both. External models.dev fields are `html.escape`d;
provenance is shown as a sub-CONFIRMED pointer with an unsigned/signed badge, never a
measured verdict. Reads the published artifact only (T7 — no probe internals imported).

## Launch gates (Gate 1 is the real blocker)

1. Legal standing — counsel clears named-vendor verdicts (Together's benchmarking
   ban is the sharpest edge). Inputs: `DISCLOSURE.md` + `docs/tos-notes.md` in
   the provenance-probe repo.
2. Negative-control false-positive rate published.
3. Evidence signing (cosign/Rekor).

## Build order (from the eng review's implementation tasks)

1. Engine-contract tests + fingerprint fix — **DONE** (provenance-probe 0.4.1).
2. Legal + DISCLOSURE.md groundwork — **DONE** (draft, pending counsel).
3. Runner: wire `assess` call, path mapping, retry/no-verdict, spend guard.
4. Drift → advisory pipeline (staging repo, dedup, promotion) — **DONE**;
   persisted via the private staging repo round-trip, DRAFT-only until an operator
   flips `OBSERVATORY_ADVISORY_LIVE=1` after a dry-run week (#50).
5. Pages site (Variant C), neutral-only until gated.
6. Negative control + FP rate (**DONE** — controls self-test, 0/2 FP);
   evidence signing (**DONE** — manifest + cosign/Rekor); probe randomization
   (**DONE** — engine `--variant-seed`, wired via `OBSERVATORY_VARIANT_SEED`).
