#!/usr/bin/env python3
"""Drift -> advisory pipeline (full-transparency posture).

The observatory publishes its complete work as collected. Advisories carry the
interpreted verdict + supporting evidence and are promoted immediately:

  - Drift opens (or appends to) a DRAFT advisory in the staging area
    (STAGING_DIR). Drafts are a working area for deduping repeat drift into a
    single open advisory; there is no withholding tier and no disclosure-window
    delay before publication.
  - The vendor is notified at detection time (a notice artifact is written with
    the evidence manifest hash; actual delivery is an ops step). The notice
    carries a standing correction/retraction offer — an operator can always
    dispute and get a prominent correction.
  - A maintainer PROMOTES the advisory, which assigns its number MPA-YYYY-NNN.
    The number is assigned AT PROMOTION, so unpromoted drafts leave no gaps in
    the public sequence.

Dedup: one open advisory per target per unresolved change; repeat drift appends
to the open advisory rather than opening a new one.

Baseline advance is delegated to lib.baseline: a NORMAL close advances the
pinned baseline; an UNSTABLE-triggered close does NOT.

State + advisories persist as JSON under STAGING_DIR/<target>/. The MPA counter
is STAGING_DIR/advisory-counter.json. This is filesystem-backed; pushing the
staging dir to a real private git repo (least-privilege PAT, staging repo only)
is an ops wrapper, not required by this module.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import baseline  # noqa: E402

STAGING_DIR = os.environ.get(
    "OBSERVATORY_STAGING_DIR",
    os.path.join(os.path.expanduser("~"), ".provenance-observatory-staging"))
DISCLOSURE_WINDOW_DAYS = 0   # full transparency: no withholding delay before publication


# --- time (injectable for tests) -------------------------------------------

def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


# --- paths -----------------------------------------------------------------

def _target_dir(target_name: str) -> str:
    d = os.path.join(STAGING_DIR, target_name)
    os.makedirs(os.path.join(d, "advisories"), exist_ok=True)
    return d


def _state_path(target_name: str) -> str:
    return os.path.join(_target_dir(target_name), "state.json")


def _advisory_path(target_name: str, staging_id: str) -> str:
    return os.path.join(_target_dir(target_name), "advisories", f"{staging_id}.json")


# --- state persistence (lib.baseline.TargetState <-> json) -----------------

def load_state(target_name: str) -> baseline.TargetState:
    p = _state_path(target_name)
    if not os.path.exists(p):
        return baseline.TargetState(pinned_baseline=None)
    with open(p) as f:
        d = json.load(f)
    return baseline.TargetState(
        pinned_baseline=d.get("pinned_baseline"),
        status=d.get("status", "STABLE"),
        open_advisory_id=d.get("open_advisory_id"),
        recent_fingerprints=tuple(d.get("recent_fingerprints", [])),
    )


def save_state(target_name: str, state: baseline.TargetState) -> None:
    d = asdict(state)
    d["recent_fingerprints"] = list(state.recent_fingerprints)
    with open(_state_path(target_name), "w") as f:
        json.dump(d, f, indent=2)


# --- helpers ---------------------------------------------------------------

def ensure_baseline(target_name: str, fingerprint: str) -> None:
    """Initialize the state machine's pinned baseline on first sight, so it
    stays consistent with the runner's monitor baseline.json. No-op if already set."""
    state = load_state(target_name)
    if state.pinned_baseline is None:
        save_state(target_name, baseline.TargetState(pinned_baseline=fingerprint))


def evidence_manifest_sha256(evidence: dict) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def _staging_id(target_name: str, current_fp: str, now: datetime) -> str:
    return f"{target_name}-{now.date().isoformat()}-{current_fp[:8]}"


def _write_advisory(target_name: str, adv: dict) -> None:
    with open(_advisory_path(target_name, adv["staging_id"]), "w") as f:
        json.dump(adv, f, indent=2)


def _load_advisory(target_name: str, staging_id: str) -> dict:
    with open(_advisory_path(target_name, staging_id)) as f:
        return json.load(f)


def _notify_vendor(target_name: str, adv: dict) -> None:
    """Write a disclosure notice artifact to staging (delivery is an ops step)."""
    notice = (
        f"Provenance Observatory — publication notice\n"
        f"Target: {target_name}\n"
        f"Opened: {adv['opened_at']}\n"
        f"Evidence manifest SHA-256: {adv['evidence']['evidence_manifest_sha256']}\n"
        f"Fingerprint: {adv['baseline_fingerprint']} -> {adv['current_fingerprint']}\n"
        f"This finding and its interpreted verdict are published as collected, in "
        f"full. You may dispute it at any time; a well-founded dispute is answered "
        f"with a prominent, permanent correction/retraction appended to the log.\n")
    with open(os.path.join(_target_dir(target_name),
                           f"notice-{adv['staging_id']}.txt"), "w") as f:
        f.write(notice)


# --- public API ------------------------------------------------------------

def on_drift(target_name: str, current_fp: str, evidence: dict,
             *, target_public: bool, now: datetime | None = None) -> dict:
    """A run drifted from the pinned baseline. Open/append a draft, or, if the
    target has gone UNSTABLE, auto-close the open draft (baseline NOT advanced).

    `evidence` is the interpreted tier (verdict labels, monitor changes) — it
    stays in staging. Returns a summary dict describing what happened.
    """
    now = _now(now)
    state = load_state(target_name)
    new_state = baseline.on_drift(state, current_fp)

    # UNSTABLE: auto-close any open draft, do not advance baseline.
    if new_state.status == "UNSTABLE":
        if state.open_advisory_id:
            adv = _load_advisory(target_name, state.open_advisory_id)
            adv["status"] = "closed-unstable"
            adv["history"].append({"ts": now.isoformat(), "event": "closed-unstable",
                                   "note": "target flapping; advisory closed, baseline NOT advanced"})
            _write_advisory(target_name, adv)
        save_state(target_name, new_state)
        return {"action": "unstable", "target": target_name,
                "baseline_unchanged": True}

    # Dedup: an advisory is already open for this unresolved change -> append.
    if state.status == "DRAFT_OPEN" and state.open_advisory_id:
        adv = _load_advisory(target_name, state.open_advisory_id)
        adv["history"].append({"ts": now.isoformat(), "event": "drift-again",
                               "note": f"repeat drift, fingerprint {current_fp[:8]}"})
        adv["current_fingerprint"] = current_fp
        _write_advisory(target_name, adv)
        save_state(target_name, new_state)
        return {"action": "appended", "staging_id": adv["staging_id"],
                "target": target_name}

    # New draft.
    sid = _staging_id(target_name, current_fp, now)
    manifest = dict(evidence)
    manifest["evidence_manifest_sha256"] = evidence_manifest_sha256(evidence)
    adv = {
        "staging_id": sid,
        "target": target_name,
        "status": "draft",
        "public": bool(target_public),
        "opened_at": now.isoformat(),
        "notified_at": now.isoformat(),
        "baseline_fingerprint": state.pinned_baseline,
        "current_fingerprint": current_fp,
        "evidence": manifest,
        "advisory_id": None,   # assigned at promotion
        "history": [{"ts": now.isoformat(), "event": "opened", "note": "drift detected"}],
    }
    _write_advisory(target_name, adv)
    _notify_vendor(target_name, adv)
    # record the open advisory id on the state
    new_state = baseline.TargetState(
        pinned_baseline=new_state.pinned_baseline, status="DRAFT_OPEN",
        open_advisory_id=sid, recent_fingerprints=new_state.recent_fingerprints)
    save_state(target_name, new_state)
    return {"action": "opened", "staging_id": sid, "target": target_name,
            "notified_at": adv["notified_at"]}


def on_model_switch(target_name: str, events: list, *, verdict: dict | None,
                    summary: str, severity: str, target_public: bool,
                    now: datetime | None = None) -> dict:
    """A captured session showed the served model switching identity mid-session.

    Opens a DRAFT advisory in staging (same disclosure pipeline as drift), keyed
    to the switch signature so a repeat of the same switch dedups. The
    interpreted misrepresentation verdict stays in staging until promotion.
    """
    now = _now(now)
    sig = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()[:8]
    sid = f"{target_name}-{now.date().isoformat()}-switch-{sig}"
    if os.path.exists(_advisory_path(target_name, sid)):
        return {"action": "exists", "staging_id": sid, "target": target_name}
    evidence = {"kind": "model_switch", "model_change_events": events,
                "verdict": verdict, "summary": summary, "severity": severity}
    evidence["evidence_manifest_sha256"] = evidence_manifest_sha256(evidence)
    adv = {
        "staging_id": sid, "target": target_name, "status": "draft",
        "public": bool(target_public), "opened_at": now.isoformat(),
        "notified_at": now.isoformat(),
        "baseline_fingerprint": None, "current_fingerprint": None,
        "evidence": evidence, "advisory_id": None,
        "history": [{"ts": now.isoformat(), "event": "opened",
                     "note": "mid-session model switch detected"}],
    }
    _write_advisory(target_name, adv)
    with open(os.path.join(_target_dir(target_name), f"notice-{sid}.txt"), "w") as f:
        f.write(f"Provenance Observatory — publication notice\n"
                f"Target: {target_name}\nOpened: {adv['opened_at']}\n"
                f"Finding: mid-session model-identity switch — {summary}\n"
                f"Evidence manifest SHA-256: {evidence['evidence_manifest_sha256']}\n"
                f"This finding and its interpreted verdict are published as "
                f"collected, in full. You may dispute it at any time; a "
                f"well-founded dispute is answered with a prominent, permanent "
                f"correction/retraction appended to the log.\n")
    return {"action": "opened", "staging_id": sid, "target": target_name}


def close_advisory(target_name: str, advisory_fingerprint: str,
                   *, now: datetime | None = None) -> dict:
    """Maintainer NORMAL close (resolved). Advances the pinned baseline."""
    now = _now(now)
    state = load_state(target_name)
    if state.open_advisory_id:
        adv = _load_advisory(target_name, state.open_advisory_id)
        adv["status"] = "closed"
        adv["history"].append({"ts": now.isoformat(), "event": "closed",
                               "note": "resolved; baseline advanced"})
        _write_advisory(target_name, adv)
    save_state(target_name, baseline.on_advisory_close(state, advisory_fingerprint))
    return {"action": "closed", "target": target_name,
            "new_baseline": advisory_fingerprint}


def _next_mpa_number(now: datetime) -> str:
    counter_path = os.path.join(STAGING_DIR, "advisory-counter.json")
    os.makedirs(STAGING_DIR, exist_ok=True)
    counter = {}
    if os.path.exists(counter_path):
        with open(counter_path) as f:
            counter = json.load(f)
    year = str(now.year)
    n = counter.get(year, 0) + 1
    counter[year] = n
    with open(counter_path, "w") as f:
        json.dump(counter, f, indent=2)
    return f"MPA-{year}-{n:03d}"


def promote(target_name: str, staging_id: str, *,
            now: datetime | None = None, force_window: bool = False) -> dict:
    """Maintainer action: publish the interpreted verdict + advisory.

    Full transparency: promotion is unconditional — there is no public/Gate-1
    gate and no disclosure-window delay. `force_window` is accepted for
    call-site compatibility and is now a no-op. Assigns MPA-YYYY-NNN here, so
    unpromoted drafts never consume a public number. Idempotent if already
    promoted. Returns the public advisory record to publish (site/feed).
    """
    now = _now(now)
    adv = _load_advisory(target_name, staging_id)
    if adv.get("advisory_id"):
        return _public_record(adv)   # idempotent: already promoted
    adv["advisory_id"] = _next_mpa_number(now)
    adv["status"] = "promoted"
    adv["history"].append({"ts": now.isoformat(), "event": "promoted",
                           "note": f"assigned {adv['advisory_id']}"})
    _write_advisory(target_name, adv)
    return _public_record(adv)


def _public_record(adv: dict) -> dict:
    """The record safe to publish to the public feed after promotion."""
    ev = adv.get("evidence") or {}
    rec = {
        "advisory_id": adv["advisory_id"],
        "target": adv["target"],
        "opened_at": adv["opened_at"],
        "promoted_at": adv["history"][-1]["ts"],
        "baseline_fingerprint": adv["baseline_fingerprint"],
        "current_fingerprint": adv["current_fingerprint"],
        "evidence_manifest_sha256": ev.get("evidence_manifest_sha256"),
        "verdict": ev.get("verdict"),
        "monitor_changes": ev.get("monitor_changes"),
    }
    # model-switch advisories carry their switch evidence for the site/feed.
    if ev.get("kind") == "model_switch":
        rec.update(kind="model_switch", summary=ev.get("summary"),
                   severity=ev.get("severity"),
                   model_change_events=ev.get("model_change_events"))
    return rec


__all__ = ["on_drift", "close_advisory", "promote", "load_state", "save_state",
           "evidence_manifest_sha256", "baseline"]
