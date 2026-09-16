# Changelog

All notable changes to the Provenance Observatory are recorded here.

## [Unreleased]

### Fixed — paused-target banner + engine-eval family count (readiness review 2026-09-16)

- **Paused-target banner (finding #5)**: the site led with the `chat-z-ai-webapp`
  model-switch finding (Gemini persona → GLM) with nothing signalling that the
  target is PAUSED (`authorized:false`, not probed nightly since 2026-08-05), so a
  reader assumed it was under active monitoring. `site/build.py` now loads
  `targets.yaml` (new `_load_paused_targets`) and treats any `authorized:false`
  target whose `notes` contain `PAUSED` as paused (extracting the date). A
  **"Paused — last observed <date>, not monitored nightly"** banner renders on the
  target detail page, and a small `PAUSED` qualifier appears wherever the finding
  is surfaced (index model-switch panel + advisories rail, the advisory page, and
  the advisories index). Data-driven — no z.ai special-case; any future paused
  target gets the same treatment.
- **Engine-eval "35 of 25" family count (cosmetic transparency bug)**:
  `scripts/refresh-engine-eval.sh` counted parenthesised eval *cases* (not
  distinct families) and hardcoded `reference_families_total: 25` while the engine
  ships 27, so the assurance panel rendered a nonsensical "N of M" with N > M. The
  summary logic is factored into an importable, unit-tested helper
  `scripts/engine_eval_summary.py`: `vocab_families_exercised` is now the count of
  DISTINCT vocab families among the origin→family consistency cases (excluding the
  accuracy-tier hostname cases that also carry parentheses), and
  `reference_families_total` is read from the engine's `tokenizer_ref.json`
  (falling back to the exercised count), clamped so N > M can never render again.
  Regenerated `data/engine_eval.json` now reads **17 of 27**.

### Added — drift persistence (#50)

- **`lib/staging_sync.py`**: private staging-repo round-trip. When `STAGING_PAT`
  and `OBSERVATORY_STAGING_REPO` are set, the nightly run clones the private
  staging repo into `STAGING_DIR` before processing targets and pushes it back
  after — so `baseline.json`, `state.json` (`TargetState` + UNSTABLE damper),
  draft advisories, and the MPA counter **persist between runs**. Silent-swap
  detection was previously inert on GitHub-hosted runners (empty staging dir
  every job → re-seeded baseline nightly, drift never fired). Least-privilege
  PAT (staging repo only); never logged; scrubbed from the cloned remote config.
- **Public transparency artifact** `data/<target>/pinned.json` =
  `{target, fingerprint_id, pinned_at}` — the pinned `fingerprint_id` only, no
  gated token counts. Does not drive the diff (the private `baseline.json` does).
- **Migration seed**: on a first run with an empty staging repo, each target's
  `baseline.json`/`state.json` is seeded from its most recent committed
  `verdict.json` (day-1 does not re-seed drift from nothing). The 45 days of
  append-only history stays; real drift detection begins from the seed.
- **Dry-run gate `OBSERVATORY_ADVISORY_LIVE` (default `0`)** — the safety
  non-negotiable. `monitor.fingerprint()` hashes header/error shape, so CDN or
  gateway header changes flip the fingerprint with no model change. Default off:
  drift opens/updates a DRAFT + logs a `::notice::`, but never promotes to a
  numbered public `MPA-YYYY-NNN`, writes `data/advisories/`, or fires the
  GitHub-issue alert. `=1` enables full promotion (guarded by the same
  via_omniroute/CONTRADICTED quarantine machine guard as `runner/promote.py`).

### Security

- The staging PAT is least-privilege (staging repo only), never logged or echoed,
  never written to public `data/`, and scrubbed from `.git/config` after clone;
  git errors are raised with the authed URL/token redacted.
- The DRAFT-only default means a merged-but-imperfect version can never emit a
  false PUBLIC advisory — public promotion is an explicit operator opt-in.

### Docs

- `docs/ARCHITECTURE.md`: new "Drift persistence" section (round-trip, pinned.json,
  migration seed, dry-run gate, real-drift-vs-wire-noise + T9 discipline).
- `.github/workflows/observatory.yml`: the `STAGING_PAT` comment is now accurate;
  added `OBSERVATORY_STAGING_REPO` + `OBSERVATORY_ADVISORY_LIVE`.
