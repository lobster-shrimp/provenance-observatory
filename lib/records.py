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
    """target -> [(date_str, record)] sorted ascending, hot window only."""
    cutoff = date.today() - timedelta(days=HOT_WINDOW_DAYS)
    out: dict[str, list[tuple[str, dict]]] = {}
    for verdict_path in glob.glob(os.path.join(data_dir, "*", "*", "verdict.json")):
        parts = verdict_path.split(os.sep)
        target, dstr = parts[-3], parts[-2]
        try:
            if date.fromisoformat(dstr) < cutoff:
                continue
        except ValueError:
            continue
        with open(verdict_path) as f:
            rec = json.load(f)
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
