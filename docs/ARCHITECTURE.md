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
4. Drift → advisory pipeline (staging repo, dedup, promotion).
5. Pages site (Variant C), neutral-only until gated.
6. Negative control + FP rate; probe randomization; signing.
