"""Pages renderer: renders from data/, withholds interpreted verdicts until a
promoted advisory exists, shows control checks, and never leaks raw verdict
labels for un-promoted targets."""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "site"))
import build  # noqa: E402


def _write_verdict(data_dir, target, kind, rec):
    d = os.path.join(data_dir, target, date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    rec.setdefault("target", {"name": target, "kind": kind, "model": "m1"})
    with open(os.path.join(d, "verdict.json"), "w") as f:
        json.dump(rec, f)


def test_renders_neutral_and_withholds_interpreted(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "openrouter-neutral-endpoint", "aggregator",
                   {"schema_version": "0.1.0", "fingerprint_id": "abc123def456",
                    "drift_seen": False})
    out = build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    doc = open(out).read()
    assert "PROVENANCE OBSERVATORY" in doc
    assert "openrouter-neutral-endpoint" in doc
    assert "abc123def456"[:12] in doc
    # interpreted verdict withheld (no promoted advisory)
    assert "withheld" in doc
    assert "CONFIRMED" not in doc


def test_promoted_advisory_shows_verdict(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "openrouter-neutral-endpoint", "aggregator",
                   {"fingerprint_id": "fp1", "drift_seen": True})
    advdir = data / "advisories"
    advdir.mkdir(parents=True)
    with open(advdir / "a1.json", "w") as f:
        json.dump({"advisory_id": "MPA-2026-001",
                   "target": "openrouter-neutral-endpoint",
                   "promoted_at": "2026-08-25T00:00:00",
                   "verdict": {"provenance_risk": {"verdict": "CONFIRMED", "confidence": "high"},
                               "jurisdictional_risk": {"verdict": "LIKELY"}}}, f)
    out = build.build(str(data), str(tmp_path / "out"), now_iso="2026-08-25T12:00:00")
    doc = open(out).read()
    assert "MPA-2026-001" in doc
    assert "CONFIRMED" in doc and "LIKELY" in doc


def test_cleared_target_shows_verdict_columns(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "control-qwen-known-answer", "control-positive",
                   {"fingerprint_id": "fpc", "drift_seen": False,
                    "verdict": {"provenance": "LIKELY", "jurisdiction": "UNLIKELY",
                                "confidence": "high"}})
    out = build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    doc = open(out).read()
    # verdict badges rendered for the cleared target (not withheld)
    assert '<span class="badge' in doc
    assert "LIKELY" in doc and "UNLIKELY" in doc and "high" in doc


def test_control_check_rendered(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "control-qwen-known-answer", "control-positive",
                   {"fingerprint_id": "fpc", "drift_seen": False,
                    "control_check": {"kind": "control-positive", "pass": True}})
    out = build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    doc = open(out).read()
    assert "control: PASS" in doc


def test_empty_data_renders_placeholder(tmp_path):
    out = build.build(str(tmp_path / "data"), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    doc = open(out).read()
    assert "No probe data yet" in doc
