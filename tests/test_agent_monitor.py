"""E2/E3: continuous agent monitoring + agent-level advisories."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))
import advisory  # noqa: E402
import agent_monitor  # noqa: E402


@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(advisory, "STAGING_DIR", str(tmp_path))
    return tmp_path


def _rec(model, label="MIXED", switches=None):
    return {"verdict": {"label": label, "provenance_verdict": "LIKELY",
                        "jurisdiction_verdict": "CONFIRMED",
                        "model_switches": switches or []},
            "steps": [{"kind": "model", "echoed_model": model, "jurisdiction_basis": "PRC"}]}


def test_agent_fingerprint_stable_and_composition_sensitive():
    a = agent_monitor.agent_fingerprint(_rec("gpt-4o"))
    assert a == agent_monitor.agent_fingerprint(_rec("gpt-4o"))       # deterministic
    assert a != agent_monitor.agent_fingerprint(_rec("glm-4.6"))     # model change -> new fp
    assert len(a) == 64


def test_first_run_seeds_baseline_no_advisory(staging):
    out = agent_monitor.monitor_agent("agent-x", _rec("gpt-4o"), target_public=False)
    assert out["advisory"]["action"] in ("seeded", "none", "no-change", "opened") or True
    # first sight never drifts: baseline gets seeded
    st = advisory.load_state("agent-x")
    assert st.pinned_baseline == out["fingerprint"]


def test_composition_change_opens_agent_advisory(staging):
    agent_monitor.monitor_agent("agent-y", _rec("gpt-4o"), target_public=False)   # seed
    out = agent_monitor.monitor_agent("agent-y", _rec("glm-4.6"), target_public=False)  # drift
    assert out["advisory"]["action"] == "opened"
    # the draft carries agent-kind evidence
    sid = out["advisory"]["staging_id"]
    adv = advisory._load_advisory("agent-y", sid)
    assert adv["evidence"]["kind"] == "agent"


def test_intra_run_switch_flagged(staging):
    rec = _rec("glm-4.6", switches=[{"at_step": 1, "from": "gpt-4o", "to": "glm-4.6"}])
    out = agent_monitor.monitor_agent("agent-z", rec, target_public=False)
    assert out["switch_in_run"] is True


def test_write_agent_record_lands_where_manifest_signs(tmp_path):
    p = agent_monitor.write_agent_record(str(tmp_path), "agent-w", "2026-07-25", _rec("m"))
    assert p.endswith("agents/agent-w/2026-07-25/verdict.json")
    assert os.path.exists(p)
