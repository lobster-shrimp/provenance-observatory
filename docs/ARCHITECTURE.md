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

> **Note:** T5's two-tier withholding + disclosure-window was **reversed to full
> transparency** — the observatory now publishes the complete work (measurements
> AND the interpreted verdict) as collected. Accuracy is served by transparency +
> safeguards (controls, published FP rate, confidence labels, corrections), not by
> hiding evidence. The P2b policy below is the one narrow exception: what the
> *signer* will and won't certify.

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
6. Negative control + FP rate (**DONE** — controls self-test, 0/2 FP);
   evidence signing (**DONE** — manifest + cosign/Rekor); probe randomization
   (**DONE** — engine `--variant-seed`, wired via `OBSERVATORY_VARIANT_SEED`).
