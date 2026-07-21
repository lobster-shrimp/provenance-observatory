"""Locks the two-tier split: interpreted verdict never leaks into the public
record, and gated records are only publishable when the target is public."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import verdict  # noqa: E402


def _bundle():
    return {
        "target": {"name": "t"}, "timestamp": "2026-07-21",
        "tokenizer": {"vector": {"p1": 1}}, "headers": {"header_shape_hash": "h"},
        "fingerprint_id": "fp0",
        "score": {"provenance_risk": {"verdict": "CONFIRMED"}},
        "user_warning": {"headline": "Chinese-origin"},
        "tokenizer_match": [{"model": "Qwen2/Qwen2.5", "score": 0.99}],
        "_drift_seen": True,
    }


def test_public_record_has_no_interpretation():
    pub, _ = verdict.split(_bundle(), target_public=False)
    for accusatory in ("score", "user_warning", "tokenizer_match"):
        assert accusatory not in pub
    assert pub["fingerprint_id"] == "fp0"
    assert pub["drift_seen"] is True
    assert pub["schema_version"] == verdict.SCHEMA_VERSION


def test_gated_record_not_publishable_when_target_private():
    _, gated = verdict.split(_bundle(), target_public=False)
    assert gated["publishable"] is False
    assert gated["score"]["provenance_risk"]["verdict"] == "CONFIRMED"


def test_gated_record_publishable_when_target_public():
    _, gated = verdict.split(_bundle(), target_public=True)
    assert gated["publishable"] is True
