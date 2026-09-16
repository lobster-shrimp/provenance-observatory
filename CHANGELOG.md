# Changelog

All notable changes to the Provenance Observatory are recorded here.

## [Unreleased]

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
