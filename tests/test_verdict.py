"""Locks the full-transparency split: the public record carries the measurements
AND the interpreted verdict (a `verdict` block) as collected, regardless of the
target_public flag; the second (gated) element mirrors it and is always
publishable."""
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


def test_public_record_includes_interpretation():
    # Full transparency: the public record carries the measurements AND the
    # interpreted keys as collected, regardless of target_public.
    pub, _ = verdict.split(_bundle(), target_public=False)
    for interpreted in ("score", "user_warning", "tokenizer_match"):
        assert interpreted in pub
    assert pub["fingerprint_id"] == "fp0"
    assert pub["drift_seen"] is True
    assert pub["schema_version"] == verdict.SCHEMA_VERSION


def test_gated_record_publishable_when_target_private():
    # Nothing is withheld: the mirror record is always publishable.
    _, gated = verdict.split(_bundle(), target_public=False)
    assert gated["publishable"] is True
    assert gated["score"]["provenance_risk"]["verdict"] == "CONFIRMED"


def test_gated_record_publishable_when_target_public():
    _, gated = verdict.split(_bundle(), target_public=True)
    assert gated["publishable"] is True


def test_public_target_exposes_verdict_block():
    b = _bundle()
    b["score"]["jurisdictional_risk"] = {"verdict": "LIKELY"}
    b["score"]["confidence"] = "high"
    pub, _ = verdict.split(b, target_public=True)
    assert pub["verdict"]["provenance"] == "CONFIRMED"
    assert pub["verdict"]["jurisdiction"] == "LIKELY"
    assert pub["verdict"]["confidence"] == "high"


def test_private_target_still_has_verdict_block():
    # Full transparency: the verdict block is published even when target_public
    # is False — the flag no longer withholds anything.
    pub, _ = verdict.split(_bundle(), target_public=False)
    assert pub["verdict"]["provenance"] == "CONFIRMED"
