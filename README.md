# Provenance Observatory

Public, continuous, evidence-backed monitoring of what actually serves a given
LLM API endpoint — and whether it is Chinese-origin or PRC-jurisdiction. Built
on [provenance-probe](https://github.com/lobster-shrimp/provenance-probe) as a
black-box CLI dependency.

## In plain terms

AI vendors can quietly change which model answers your API calls — swapping in a
cheaper model, rerouting to someone else's servers, or reselling a Chinese-made
model under a Western name — and you would normally never know. The
`provenance-probe` engine can fingerprint an endpoint and tell what is really
running it. This project runs that check **every night, in public, and keeps the
receipts**: it publishes the raw measurements as an append-only, signed log
(like a public tamper-proof ledger), and when it spots that an endpoint has
changed, it opens a numbered advisory — but only *after* privately notifying the
operator and giving them time to respond. The goal is a citable public record
that compliance and procurement teams can point to, not a rumor mill: verdicts
come with confidence levels and a measured error rate, and nothing accusatory
about a named company is published until a lawyer has cleared it.

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

## Standing up the controls (do this first — zero ToS risk)

Controls are self-hosted endpoints you own, so they need no legal clearance.
Start collecting a false-positive baseline today:

1. **Validate the pipeline** with real-tokenizer mocks (genuine GGUF counts, no
   HF account needed) — this is the automated **Gate-2 false-positive check**:
   ```bash
   PROVENANCE_PROBE_SRC=../provenance-probe ./scripts/controls-selftest.sh
   # -> asserts Qwen2 identified, Llama-3 not flagged CN, 0 false positives
   ```
   Verified locally: positive control (Qwen2) identified at score 1.0; negative
   control (Llama-3, US) not flagged Chinese — **0/2 false positives**.

2. **Point at your real endpoints:** in `targets.yaml`, replace the two
   `SELF_HOSTED_URL_TBD` control `base_url`s with your self-hosted Qwen and
   Western model endpoints (and set the `auth_env` keys if they need a token).

3. **Collect:**
   ```bash
   python runner/run.py --targets targets.yaml   # controls only by default
   ```
   Each run writes neutral evidence to `data/<target>/<date>/verdict.json` and a
   `control_check` (pass/fail). Accumulate these to establish your published
   false-positive rate — a launch gate for any real-vendor verdict.

Commercial targets stay off until `OBSERVATORY_PROBE_COMMERCIAL=1` AND Gate 1
counsel clearance.

### Probe randomization (optional hardening)

Because the probe corpus is public, set `OBSERVATORY_VARIANT_SEED=N` to rotate
the exact bytes sent on the wire (defeats exact-string special-casing by a
monitored vendor). The engine reference must be built for the same seed
(`build-reference --variant-seed N`), so rotating means rebuilding the reference
the workflow installs. Seed 0 (default) is the canonical corpus.

See `docs/ARCHITECTURE.md` for the full decision record and the source design doc.
