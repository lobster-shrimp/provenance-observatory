"""Drift persistence (issue #50): migration seed, the public pinned.json
transparency artifact, the DRAFT-only dry-run gate, and the persisted UNSTABLE
damper / T9 baseline discipline across runs."""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))
import run  # noqa: E402
import advisory  # noqa: E402
from lib import baseline  # noqa: E402

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)

TARGET = {"name": "vendor-x", "kind": "webapp", "base_url": "https://x",
          "authorized": True, "public": False}
DEFAULTS = {"layers": ["tokenizer", "wire"]}


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data"
    staging = tmp_path / "staging"
    data.mkdir()
    staging.mkdir()
    monkeypatch.setattr(run, "DATA_DIR", str(data))
    monkeypatch.setattr(run, "STAGING_DIR", str(staging))
    monkeypatch.setattr(advisory, "STAGING_DIR", str(staging))
    monkeypatch.delenv("OBSERVATORY_ADVISORY_LIVE", raising=False)
    return data, staging


def _write_verdict(data, name, day, fingerprint):
    d = data / name / day
    d.mkdir(parents=True)
    (d / "verdict.json").write_text(json.dumps({
        "schema_version": "0.1.0", "target": name, "fingerprint_id": fingerprint,
        "tokenizer": {"vector": {"cjk_dense": 93}, "usable": True},  # gated content
    }))


# --- migration seed ---------------------------------------------------------

def test_seed_from_history(sandbox):
    data, staging = sandbox
    _write_verdict(data, "vendor-x", "2026-09-01", "fp_old")
    _write_verdict(data, "vendor-x", "2026-09-10", "fp_recent")  # most recent wins
    assert run.seed_state_from_history("vendor-x") is True
    # baseline.json = raw bundle monitor diffs against (from newest verdict)
    base = json.load(open(os.path.join(str(staging), "vendor-x", "baseline.json")))
    assert base["fingerprint_id"] == "fp_recent"
    # state pinned to that fingerprint
    assert advisory.load_state("vendor-x").pinned_baseline == "fp_recent"
    # idempotent: does NOT clobber persisted state on a later run
    assert run.seed_state_from_history("vendor-x") is False


def test_seed_noop_without_history(sandbox):
    assert run.seed_state_from_history("nobody") is False


# --- pinned.json transparency artifact --------------------------------------

def test_pinned_json_has_only_public_fields(sandbox):
    data, staging = sandbox
    advisory.save_state("vendor-x", baseline.TargetState(pinned_baseline="fp_pinned"))
    run.write_pinned("vendor-x")
    pinned = json.load(open(os.path.join(str(data), "vendor-x", "pinned.json")))
    assert set(pinned.keys()) == {"target", "fingerprint_id", "pinned_at"}
    assert pinned["fingerprint_id"] == "fp_pinned"
    # no gated content leaked
    assert "tokenizer" not in pinned and "vector" not in json.dumps(pinned)


# --- dry-run gate (the non-negotiable default) ------------------------------

def _run_drift(sandbox, monkeypatch, live):
    data, staging = sandbox
    monkeypatch.setenv("OBSERVATORY_PROBE_COMMERCIAL", "1")
    if live:
        monkeypatch.setenv("OBSERVATORY_ADVISORY_LIVE", "1")
    advisory.save_state("vendor-x", baseline.TargetState(pinned_baseline="fp_old"))
    monkeypatch.setattr(run, "run_assess", lambda t, d: {
        "fingerprint_id": "fp_new", "target": t["name"],
        "score": {"provenance_risk": {"verdict": "CONFIRMED"}}})
    monkeypatch.setattr(run, "check_drift",
                        lambda n, c: (True, [{"field": "fingerprint_id"}]))
    run.process_target(TARGET, DEFAULTS, {})
    return data, staging


def test_dry_run_default_stays_draft_no_public_advisory(sandbox, monkeypatch):
    data, staging = _run_drift(sandbox, monkeypatch, live=False)
    # a DRAFT exists in private staging...
    drafts = os.listdir(os.path.join(str(staging), "vendor-x", "advisories"))
    assert len(drafts) == 1
    # ...but NO public advisory is emitted (default OFF)
    assert not os.path.isdir(os.path.join(str(data), "advisories"))
    # the draft carries no assigned MPA number
    adv = json.load(open(os.path.join(str(staging), "vendor-x", "advisories", drafts[0])))
    assert adv["advisory_id"] is None


def test_live_gate_promotes_public_advisory(sandbox, monkeypatch):
    data, staging = _run_drift(sandbox, monkeypatch, live=True)
    pub = os.listdir(os.path.join(str(data), "advisories"))
    assert len(pub) == 1 and pub[0].startswith("MPA-")
    rec = json.load(open(os.path.join(str(data), "advisories", pub[0])))
    assert rec["target"] == "vendor-x"
    assert rec["verdict"]["provenance_risk"]["verdict"] == "CONFIRMED"


def test_contradicted_drift_never_auto_promotes_even_when_live(sandbox, monkeypatch):
    # P2b: a CONTRADICTED router-vs-fingerprint cross-check is an accusation about
    # a named third party — quarantined for human review, never auto-published,
    # and it must consume NO MPA number.
    data, staging = sandbox
    monkeypatch.setenv("OBSERVATORY_PROBE_COMMERCIAL", "1")
    monkeypatch.setenv("OBSERVATORY_ADVISORY_LIVE", "1")
    advisory.save_state("vendor-x", baseline.TargetState(pinned_baseline="fp_old"))
    monkeypatch.setattr(run, "run_assess", lambda t, d: {
        "fingerprint_id": "fp_new", "target": t["name"],
        "score": {"provenance_risk": {"verdict": "CONFIRMED"}},
        "measurement_path": "via_omniroute",
        "omniroute": {"cross_check": {"state": "CONTRADICTED"}}})
    monkeypatch.setattr(run, "check_drift",
                        lambda n, c: (True, [{"field": "fingerprint_id"}]))
    run.process_target(TARGET, DEFAULTS, {})
    # DRAFT staged, but NO public advisory, and no MPA number consumed.
    assert not os.path.isdir(os.path.join(str(data), "advisories"))
    assert not os.path.exists(os.path.join(str(staging), "advisory-counter.json"))


# --- persisted UNSTABLE damper + T9 across runs -----------------------------

EVID = {"verdict": {"provenance_risk": {"verdict": "CONFIRMED"}}, "monitor_changes": []}


def test_persisted_unstable_damper_across_runs(sandbox):
    _data, staging = sandbox
    advisory.save_state("vendor-x", baseline.TargetState(
        pinned_baseline="fp0", recent_fingerprints=("fp1",)))
    # three nightly "runs" — each reloads state from the persisted file on disk
    advisory.on_drift("vendor-x", "fp1", EVID, target_public=False, now=NOW)
    advisory.on_drift("vendor-x", "fp2", EVID, target_public=False, now=NOW + timedelta(days=1))
    r = advisory.on_drift("vendor-x", "fp3", EVID, target_public=False, now=NOW + timedelta(days=2))
    assert r["action"] == "unstable"
    # reload from disk proves the damper state persisted, not just in-memory
    reloaded = advisory.load_state("vendor-x")
    assert reloaded.status == "UNSTABLE"
    # T9: an UNSTABLE-triggered close does NOT advance the pinned baseline
    assert reloaded.pinned_baseline == "fp0"


def test_t9_normal_close_advances_baseline_round_trip(sandbox):
    advisory.save_state("vendor-x", baseline.TargetState(pinned_baseline="fp0"))
    advisory.on_drift("vendor-x", "fp1", EVID, target_public=False, now=NOW)
    advisory.close_advisory("vendor-x", advisory_fingerprint="fp1", now=NOW + timedelta(days=1))
    reloaded = advisory.load_state("vendor-x")
    assert reloaded.status == "STABLE" and reloaded.pinned_baseline == "fp1"
