# Provenance Observatory

Public, continuous, evidence-backed monitoring of what actually serves a given
LLM API endpoint — and whether it is Chinese-origin or PRC-jurisdiction. Built
on [provenance-probe](https://github.com/lobster-shrimp/provenance-probe) as a
black-box CLI dependency.

> **Status: SCAFFOLD — not live.** No public accusatory verdict ships until the
> launch gates below pass. This repo currently wires the structure and the
> load-bearing design decisions; the sections marked TODO in the code are the
> real implementation.

## What it does

Nightly, it probes a small set of endpoints and commits the results to git as an
append-only, tamper-evident log (Certificate Transparency for model provenance).
It publishes measurements immediately and gates accusations:

- **Neutral evidence** (token counts, headers, latency, `fingerprint_id`, the
  fact that drift occurred) — public immediately.
- **Interpreted verdict** (CONFIRMED/LIKELY jurisdiction + provenance) — withheld
  behind a responsible-disclosure window and released only after Gate 1 clears
  the target. See `lib/verdict.py`.

Verdict *changes* become numbered advisories (MPA-YYYY-NNN) practitioners can
cite in ATO packages and procurement memos.

## Launch gates (nothing accusatory goes public until all pass)

1. **Legal standing** — publishing entity + counsel sign-off on named-vendor
   verdicts (esp. Together's explicit benchmarking ban). Groundwork:
   `DISCLOSURE.md` + `docs/tos-notes.md` in the provenance-probe repo.
2. **Measured false-positive rate** — the known-answer + negative controls in
   `targets.yaml` must pass before any real-vendor verdict publishes.
3. **Evidence signing** — cosign/Rekor over manifests before claiming CT
   properties publicly.

## Layout

| Path | Role |
|------|------|
| `targets.yaml` | Monitored targets; `public` gate + spend budget (U1/U2) |
| `runner/run.py` | Nightly runner — shells out to provenance-probe CLI (T7) |
| `runner/advisory.py` | Drift → draft advisory in private staging → promotion |
| `lib/verdict.py` | Two-tier split: neutral vs interpreted (T5) |
| `lib/baseline.py` | Baseline lifecycle + UNSTABLE state machine (T9) |
| `site/build.py` | Variant C Pages renderer (neutral-only until gated) |
| `.github/workflows/observatory.yml` | Nightly cron; secrets posture |

## Local dev

```bash
pip install "provenance-probe==0.4.1" pyyaml pytest
python runner/run.py --targets targets.yaml   # scaffold: prints wired call shapes
pytest                                         # lib logic tests
```

See `docs/ARCHITECTURE.md` for the full decision record and the source design doc.
