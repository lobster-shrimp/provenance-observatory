"""Locks the baseline/UNSTABLE rules — especially the round-3 review bug:
an UNSTABLE-triggered close must NOT advance the pinned baseline.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import baseline as b  # noqa: E402


def test_drift_opens_draft():
    s = b.TargetState(pinned_baseline="fp0")
    s2 = b.on_drift(s, "fp1")
    assert s2.status == "DRAFT_OPEN"
    assert s2.pinned_baseline == "fp0"  # unchanged until close


def test_normal_close_advances_baseline():
    s = b.TargetState(pinned_baseline="fp0", status="DRAFT_OPEN", open_advisory_id="A1")
    s2 = b.on_advisory_close(s, advisory_fingerprint="fp1")
    assert s2.status == "STABLE"
    assert s2.pinned_baseline == "fp1"


def test_unstable_close_does_not_advance_baseline():
    # 3 distinct fingerprints trips UNSTABLE; baseline must stay pinned.
    s = b.TargetState(pinned_baseline="fp0", recent_fingerprints=("fp0", "fp1"))
    s2 = b.on_drift(s, "fp2")  # now fp0,fp1,fp2 → 3 distinct
    assert s2.status == "UNSTABLE"
    assert s2.pinned_baseline == "fp0", "UNSTABLE close must NOT advance the baseline"
    assert s2.open_advisory_id is None  # draft auto-closed


def test_rebless_after_stability():
    s = b.TargetState(pinned_baseline="fp0", status="UNSTABLE")
    s2 = b.on_stability_reblessing(s, current_fp="fpZ")
    assert s2.status == "STABLE"
    assert s2.pinned_baseline == "fpZ"


def test_dedup_second_drift_keeps_one_open_advisory():
    s = b.TargetState(pinned_baseline="fp0", status="DRAFT_OPEN",
                      open_advisory_id="A1", recent_fingerprints=("fp0", "fp1"))
    s2 = b.on_drift(s, "fp1")  # same drifted fp; not a new distinct → stays DRAFT_OPEN
    assert s2.status == "DRAFT_OPEN"
    assert s2.open_advisory_id == "A1"
