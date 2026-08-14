"""Canonical readers for the committed evidence tree in data/.

Single source of truth for both the static site (site/build.py) and the live
API (api/). Everything here reads the PUBLIC, already-two-tier-split records
(data/<target>/<date>/verdict.json) — the interpreted/gated tier lives only in
private staging and is never touched here, so any consumer is Gate-1-consistent
by construction.
"""
from __future__ import annotations
import glob
import json
import os
from datetime import date, timedelta

HOT_WINDOW_DAYS = 90


def load_target_records(data_dir: str) -> dict[str, list[tuple[str, dict]]]:
    """target -> [(date_str, record)] sorted ascending, hot window only.

    QUARANTINED records (proxy measurements without a passing calibration, or
    CONTRADICTED cross-checks) are excluded — they must never render as a public
    verdict on the site or the API (both read through here). They remain visible
    in the transparency-log quarantine section via the signed manifest. This is
    the single choke point that keeps a manifest-quarantine from being cosmetic
    (Codex adversarial, HIGH).
    """
    return _load_records(data_dir, os.path.join(data_dir, "*", "*", "verdict.json"))


def load_agent_records(data_dir: str) -> dict[str, list[tuple[str, dict]]]:
    """agent target -> [(date_str, record)] for the agent flight-recorder evidence
    under data/agents/<target>/<date>/verdict.json (one level deeper than endpoint
    records). Hot window only; quarantined records excluded (see load_target_records)."""
    return _load_records(data_dir, os.path.join(data_dir, "agents", "*", "*", "verdict.json"))


def _load_records(data_dir: str, pattern: str) -> dict[str, list[tuple[str, dict]]]:
    from . import publish_policy
    cutoff = date.today() - timedelta(days=HOT_WINDOW_DAYS)
    out: dict[str, list[tuple[str, dict]]] = {}
    for verdict_path in glob.glob(pattern):
        parts = verdict_path.split(os.sep)
        target, dstr = parts[-3], parts[-2]
        try:
            if date.fromisoformat(dstr) < cutoff:
                continue
        except ValueError:
            continue
        with open(verdict_path) as f:
            rec = json.load(f)
        if not publish_policy.is_publishable(rec)[0]:
            continue                        # quarantined -> not a public verdict
        out.setdefault(target, []).append((dstr, rec))
    for recs in out.values():
        recs.sort(key=lambda x: x[0])
    return out


def load_promoted_advisories(data_dir: str) -> dict[str, dict]:
    """target -> latest promoted public advisory record, if any."""
    latest: dict[str, dict] = {}
    for p in glob.glob(os.path.join(data_dir, "advisories", "*.json")):
        with open(p) as f:
            adv = json.load(f)
        t = adv.get("target")
        if t and (t not in latest or adv.get("promoted_at", "") > latest[t].get("promoted_at", "")):
            latest[t] = adv
    return latest


def load_announcements(data_dir: str) -> list[dict]:
    """Release / method announcements for the RSS feed — plain news items, DISTINCT
    from numbered MPA provenance advisories (which are evidence-backed findings about
    a target). Append-only JSON in data/announcements/; newest first."""
    out: list[dict] = []
    for p in sorted(glob.glob(os.path.join(data_dir, "announcements", "*.json"))):
        try:
            with open(p) as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict):
            out.append(loaded)
    out.sort(key=lambda a: a.get("date", ""), reverse=True)
    return out


def load_transcripts(data_dir: str) -> dict[str, dict]:
    """target -> latest transcript-analysis record (mid-session model-switch
    detection). First-party misrepresentation findings publish under full
    transparency; but a transcript record is run through the SAME publication
    policy as a verdict (Codex/Claude adversarial, CRITICAL) so one that carries
    proxy (via_omniroute) or CONTRADICTED cross-check data cannot bypass the gate
    just by living in transcript.json. Hot-window only, like the other loaders."""
    from . import publish_policy
    cutoff = date.today() - timedelta(days=HOT_WINDOW_DAYS)
    latest: dict[str, dict] = {}
    for p in glob.glob(os.path.join(data_dir, "*", "*", "transcript.json")):
        parts = p.split(os.sep)
        target, dstr = parts[-3], parts[-2]
        try:
            if date.fromisoformat(dstr) < cutoff:
                continue
        except ValueError:
            continue
        try:
            with open(p) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if not publish_policy.is_publishable(rec)[0]:
            continue                        # quarantined -> not surfaced publicly
        rec["date"] = dstr
        if target not in latest or dstr > latest[target].get("date", ""):
            latest[target] = rec
    return latest


def _rekor_index(bundle_path: str) -> int | None:
    """Pull the Rekor transparency-log index out of a cosign bundle, if present.

    cosign keyless signing records the manifest in Rekor (the Sigstore
    transparency log); the inclusion proof rides in <manifest>.cosign.bundle at
    rekorBundle.Payload.logIndex. That log index IS our transparency-log proof —
    no separate Trillian needed.
    """
    try:
        with open(bundle_path) as f:
            b = json.load(f)
        return (((b.get("rekorBundle") or {}).get("Payload") or {}).get("logIndex"))
    except (OSError, ValueError, AttributeError):
        return None


def load_manifests(data_dir: str) -> list[dict]:
    """Daily signed manifests newest-first: {date, manifest_root, entries, signed,
    rekor_log_index}."""
    out = []
    for p in glob.glob(os.path.join(data_dir, "manifests", "*.json")):
        if p.endswith(".quarantine.json"):
            continue                       # sidecar, not a manifest (would render a bogus row)
        try:
            with open(p) as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        bundle = p + ".cosign.bundle"
        m["signed"] = os.path.exists(bundle)
        m["rekor_log_index"] = _rekor_index(bundle) if m["signed"] else None
        out.append(m)
    out.sort(key=lambda m: m.get("date", ""), reverse=True)
    return out
