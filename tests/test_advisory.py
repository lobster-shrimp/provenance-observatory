"""Advisory pipeline: draft/dedup/UNSTABLE, disclosure-window gating, promotion
numbering, and the two-tier boundary (interpreted evidence stays private).
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


def test_promote_refused_when_not_public(staging):
    advisory.save_state(T, baseline.TargetState(pinned_baseline="fp0"))
    r = advisory.on_drift(T, "fp1", EVID, target_public=False, now=NOW)
    with pytest.raises(PermissionError, match="not public"):
        advisory.promote(T, r["staging_id"], now=NOW + timedelta(days=40))


def test_promote_refused_before_window(staging):
    r = advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)
    with pytest.raises(PermissionError, match="window not elapsed"):
        advisory.promote(T, r["staging_id"], now=NOW + timedelta(days=5))


def test_promote_assigns_number_after_window(staging):
    r = advisory.on_drift(T, "fp1", EVID, target_public=True, now=NOW)
    pub = advisory.promote(T, r["staging_id"], now=NOW + timedelta(days=31))
    assert pub["advisory_id"] == "MPA-2026-001"
    assert pub["verdict"]["provenance_risk"]["verdict"] == "CONFIRMED"
    assert "evidence_manifest_sha256" in pub


def test_drafts_do_not_consume_advisory_numbers(staging):
    # Two drafts on two targets; only one is promoted -> it gets 001, no gap.
    advisory.save_state("t-a", baseline.TargetState(pinned_baseline="fp0"))
    advisory.save_state("t-b", baseline.TargetState(pinned_baseline="fp0"))
    advisory.on_drift("t-a", "fpX", EVID, target_public=True, now=NOW)
    rb = advisory.on_drift("t-b", "fpY", EVID, target_public=True, now=NOW)
    pub = advisory.promote("t-b", rb["staging_id"], now=NOW + timedelta(days=31))
    assert pub["advisory_id"] == "MPA-2026-001"   # t-a's unpromoted draft consumed nothing
