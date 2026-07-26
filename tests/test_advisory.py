"""Advisory pipeline: draft/dedup/UNSTABLE, immediate promotion, and MPA
numbering. Full transparency: promotion is unconditional (no public/Gate-1 gate
and no disclosure-window delay).
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import advisory  # noqa: E402
from lib import baseline  # noqa: E402

T = "openrouter-neutral-endpoint"
NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
EVID = {"verdict": {"provenance_risk": {"verdict": "CONFIRMED"}},
        "monitor_changes": [{"field": "fingerprint_id", "severity": "critical"}]}


@pytest.fixture()
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(advisory, "STAGING_DIR", str(tmp_path))
    # seed a pinned baseline so drift is meaningful
    advisory.save_state(T, baseline.TargetState(pinned_baseline="fp0"))
    return tmp_path


def test_drift_opens_draft_and_notifies(staging):
    r = advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)
    assert r["action"] == "opened"
    st = advisory.load_state(T)
    assert st.status == "DRAFT_OPEN" and st.open_advisory_id == r["staging_id"]
    # vendor notice artifact written to staging (private)
    assert os.path.exists(os.path.join(staging, T, f"notice-{r['staging_id']}.txt"))
    adv = advisory._load_advisory(T, r["staging_id"])
    assert adv["advisory_id"] is None          # no number until promotion
    assert "evidence_manifest_sha256" in adv["evidence"]


def test_repeat_drift_dedups_to_one_advisory(staging):
    r1 = advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)
    r2 = advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW + timedelta(days=1))
    assert r2["action"] == "appended"
    assert r2["staging_id"] == r1["staging_id"]
    advisories = os.listdir(os.path.join(staging, T, "advisories"))
    assert len(advisories) == 1


def test_unstable_closes_draft_and_does_not_advance_baseline(staging):
    advisory.save_state(T, baseline.TargetState(pinned_baseline="fp0",
                                                recent_fingerprints=("fp1",)))
    advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)  # open draft
    advisory.on_drift(T, "fp2", EVID, target_public=True, now=NOW + timedelta(days=1))
    r = advisory.on_drift(T, "fp3", EVID, target_public=True, now=NOW + timedelta(days=2))
    assert r["action"] == "unstable"
    st = advisory.load_state(T)
    assert st.status == "UNSTABLE"
    assert st.pinned_baseline == "fp0", "UNSTABLE must NOT advance the baseline"


def test_close_advisory_advances_baseline(staging):
    advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)
    advisory.close_advisory(T, advisory_fingerprint="fp1", now=NOW + timedelta(days=1))
    st = advisory.load_state(T)
    assert st.status == "STABLE" and st.pinned_baseline == "fp1"


def test_promote_succeeds_regardless_of_public_flag(staging):
    # Full transparency: the public flag no longer gates promotion.
    advisory.save_state(T, baseline.TargetState(pinned_baseline="fp0"))
    r = advisory.on_drift(T, "fp1", EVID, target_public=False, now=NOW)
    pub = advisory.promote(T, r["staging_id"], now=NOW)
    assert pub["advisory_id"] == "MPA-2026-001"
    assert pub["verdict"]["provenance_risk"]["verdict"] == "CONFIRMED"


def test_promote_succeeds_immediately_no_window(staging):
    # No disclosure-window delay: promotion works right away.
    r = advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)
    pub = advisory.promote(T, r["staging_id"], now=NOW)
    assert pub["advisory_id"] == "MPA-2026-001"
    assert pub["verdict"]["provenance_risk"]["verdict"] == "CONFIRMED"


def test_promote_assigns_number(staging):
    r = advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)
    pub = advisory.promote(T, r["staging_id"], now=NOW)
    assert pub["advisory_id"] == "MPA-2026-001"
    assert pub["verdict"]["provenance_risk"]["verdict"] == "CONFIRMED"
    assert "evidence_manifest_sha256" in pub


def test_promote_is_idempotent(staging):
    r = advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)
    pub1 = advisory.promote(T, r["staging_id"], now=NOW)
    pub2 = advisory.promote(T, r["staging_id"], now=NOW + timedelta(days=1))
    assert pub1["advisory_id"] == pub2["advisory_id"] == "MPA-2026-001"


def test_drafts_do_not_consume_advisory_numbers(staging):
    # Two drafts on two targets; only one is promoted -> it gets 001, no gap.
    advisory.save_state("t-a", baseline.TargetState(pinned_baseline="fp0"))
    advisory.save_state("t-b", baseline.TargetState(pinned_baseline="fp0"))
    advisory.on_drift("t-a", "fpX", EVID, target_public=True, now=NOW)
    rb = advisory.on_drift("t-b", "fpY", EVID, target_public=True, now=NOW)
    pub = advisory.promote("t-b", rb["staging_id"], now=NOW)
    assert pub["advisory_id"] == "MPA-2026-001"   # t-a's unpromoted draft consumed nothing


# --- model-switch advisories (transcript pipeline) --------------------------

SWITCH = [{"turn": 7, "from": "Google Gemini", "to": "GLM (Zhipu)", "kind": "concession"}]


def test_model_switch_opens_draft_and_promotes(staging):
    s = advisory.on_model_switch("chat-z-ai-webapp", SWITCH,
                                 verdict={"misrepresentation": True, "severity": "critical"},
                                 summary="switched to GLM (Zhipu)", severity="high",
                                 target_public=True, now=NOW)
    assert s["action"] == "opened"
    # full transparency: promotes immediately, no window to wait out
    rec = advisory.promote("chat-z-ai-webapp", s["staging_id"], now=NOW)
    assert rec["advisory_id"].startswith("MPA-")
    assert rec["kind"] == "model_switch" and rec["severity"] == "high"
    assert rec["model_change_events"][0]["to"] == "GLM (Zhipu)"


def test_model_switch_promotes_regardless_of_public_flag(staging):
    # The public flag no longer gates promotion under full transparency.
    s = advisory.on_model_switch("some-webapp", SWITCH, verdict=None,
                                 summary="switch", severity="medium",
                                 target_public=False, now=NOW)
    rec = advisory.promote("some-webapp", s["staging_id"], now=NOW)
    assert rec["advisory_id"].startswith("MPA-")
    assert rec["kind"] == "model_switch"


def test_model_switch_dedups(staging):
    a1 = advisory.on_model_switch("t", SWITCH, verdict=None, summary="x",
                                  severity="medium", target_public=True, now=NOW)
    a2 = advisory.on_model_switch("t", SWITCH, verdict=None, summary="x",
                                  severity="medium", target_public=True, now=NOW)
    assert a1["staging_id"] == a2["staging_id"] and a2["action"] == "exists"
